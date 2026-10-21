#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
#
# Matchy Wallpapers — add-on entrypoint.
#
# Turns add-on options into the engine's config.json, then execs the engine.
# The engine DEEP-MERGES this file over its own defaults, so we write only what
# the user actually set, and never write a JSON null: a null overwrites the
# default and then crashes the engine.
# bashio already sets errexit, errtrace, nounset, pipefail and `shopt -s inherit_errexit`;
# inherit_errexit is what makes every command substitution below strict. Repeated for clarity.
set -euo pipefail

readonly DATA_DIR=/data
readonly CONFIG_FILE="${DATA_DIR}/config.json"
readonly SECRETS_DIR="${DATA_DIR}/secrets"

( umask 077; mkdir -p "${SECRETS_DIR}" )
chmod 700 "${SECRETS_DIR}"
# A renamed or revoked captioner must not leave its key behind: /data survives restarts,
# updates and uninstall-with-keep-data, and it is included in Home Assistant backups.
rm -f "${SECRETS_DIR}"/*.key

# opt <key> <default> — bashio::config, but with an explicit fallback and no nulls.
opt() {
    if bashio::config.has_value "${1}"; then
        bashio::config "${1}"
    else
        printf '%s' "${2}"
    fi
}

# The container port is fixed: config.yaml's ingress_port must match it, and the user
# chooses the published host port in the UI instead.
# int <key> <default> — a non-numeric option must never reach `jq --argjson`, which would
# abort with a raw jq usage dump and (worse) after the config file was already truncated.
int() {
    local value
    value="$(opt "${1}" "${2}")"
    if [[ "${value}" =~ ^-?[0-9]+$ ]]; then
        printf '%s' "${value}"
    else
        bashio::log.warning "Option '${1}' is not a whole number ('${value}'); using ${2}."
        printf '%s' "${2}"
    fi
}

readonly PORT=8160

# An absent key makes `bashio::config` print the literal string "null", so
# `bashio::config.true` would read as false and switch captions off against the documented
# default. Resolve it once, explicitly, and reuse it.
captions_on="$(opt 'caption_enabled' true)"
[[ "${captions_on}" == "true" ]] || captions_on=false
readonly captions_on
# The same for focus_auto, whose default is off (centred crops).
focus_auto="$(opt 'focus_auto' false)"
[[ "${focus_auto}" == "true" ]] || focus_auto=false
readonly focus_auto
kids_on="$(opt 'caption_kids' false)"
[[ "${kids_on}" == "true" ]] || kids_on=false
readonly kids_on

# Colour match (the palette, accent and GNOME accent name per picture): same shape, same reason.
palette_on="$(opt 'palette_enabled' true)"
[[ "${palette_on}" == "true" ]] || palette_on=false
readonly palette_on

# ---------------------------------------------------------------- source folders
folders='[]'
usable_folders=0
if bashio::config.exists 'source_folders'; then
    for idx in $(bashio::config 'source_folders|keys'); do
        folder="$(bashio::config "source_folders[${idx}]")"
        [[ -n "${folder}" ]] || continue
        if [[ ! -d "${folder}" ]]; then
            bashio::log.warning "Source folder not found, skipping: ${folder}"
            continue
        fi
        folders="$(jq -c --arg p "${folder}" '. + [{path: $p}]' <<<"${folders}")"
        usable_folders=$((usable_folders + 1))
    done
fi

# ------------------------------------------------------------------- captioners
captioners='[]'
if bashio::config.exists 'captioners'; then
    for idx in $(bashio::config 'captioners|keys'); do
        c_url="$(opt "captioners[${idx}].url" '')"
        c_model="$(opt "captioners[${idx}].model" '')"
        if [[ -z "${c_url}" || -z "${c_model}" ]]; then
            bashio::log.warning "Captioner ${idx} has no url or no model, skipping."
            continue
        fi
        c_name="$(opt "captioners[${idx}].name" "captioner-${idx}")"
        c_timeout="$(int "captioners[${idx}].timeout" 120)"
        entry="$(jq -nc \
            --arg n "${c_name}" --arg u "${c_url}" --arg m "${c_model}" \
            --argjson t "${c_timeout}" \
            '{name: $n, url: $u, model: $m, timeout: $t}')"

        # The key goes to a 600 file inside /data and only its path enters the config, which
        # keeps it out of config.json and out of the engine's argv. Note it is still in
        # /data/options.json in plaintext, as it is for every add-on option.
        if bashio::config.has_value "captioners[${idx}].api_key"; then
            # Keyed by index, not by name: two names can sanitise to the same filename.
            key_file="${SECRETS_DIR}/captioner-${idx}.key"
            ( umask 077; bashio::config "captioners[${idx}].api_key" > "${key_file}" )
            chmod 600 "${key_file}"
            entry="$(jq -c --arg f "${key_file}" '.api_key_file = $f' <<<"${entry}")"
        fi

        captioners="$(jq -c --argjson e "${entry}" '. + [$e]' <<<"${captioners}")"
    done
fi
captioner_count="$(jq 'length' <<<"${captioners}")"
readonly captioner_count

# ------------------------------------------------------------------ picture sets
# Settings for a sub-folder of a source folder, matched by its name in every source folder:
# the id prefix, the collection name and credit the captioner is told, a context sentence, and
# `abstract` (digital or illustrated art, so the captioner never treats it as a real place).
sets='{}'
if bashio::config.exists 'sets'; then
    for idx in $(bashio::config 'sets|keys'); do
        s_folder="$(opt "sets[${idx}].folder" '')"
        [[ -n "${s_folder}" ]] || continue
        entry='{}'
        for key in id_prefix name credit context; do
            if bashio::config.has_value "sets[${idx}].${key}"; then
                entry="$(jq -c --arg k "${key}" --arg v "$(bashio::config "sets[${idx}].${key}")" \
                    '.[$k] = $v' <<<"${entry}")"
            fi
        done
        if [[ "$(opt "sets[${idx}].abstract" false)" == "true" ]]; then
            entry="$(jq -c '.abstract = true' <<<"${entry}")"
        fi
        sets="$(jq -c --arg f "${s_folder}" --argjson e "${entry}" '.[$f] = $e' <<<"${sets}")"
    done
fi

# ------------------------------------------------------------------ excluded ids
# Glob patterns on picture ids (e.g. "aether-*"): matching pictures never enter the library.
exclude='[]'
if bashio::config.exists 'exclude'; then
    for idx in $(bashio::config 'exclude|keys'); do
        pattern="$(bashio::config "exclude[${idx}]")"
        [[ -n "${pattern}" ]] || continue
        exclude="$(jq -c --arg p "${pattern}" '. + [$p]' <<<"${exclude}")"
    done
fi

# ------------------------------------------------------------------ quiet hours
# Only the fields the user set; the engine keeps quiet hours off unless start and end are both there.
# `bashio::config` rather than `opt` for hold: has_value would count an explicit false as unset.
quiet='{}'
for key in start end; do
    if bashio::config.has_value "quiet_hours.${key}"; then
        quiet="$(jq -c --arg k "${key}" --arg v "$(bashio::config "quiet_hours.${key}")" '.[$k] = $v' <<<"${quiet}")"
    fi
done
case "$(bashio::config 'quiet_hours.hold')" in
    true) quiet="$(jq -c '.hold = true' <<<"${quiet}")" ;;
    false) quiet="$(jq -c '.hold = false' <<<"${quiet}")" ;;
esac
if bashio::config.has_value 'quiet_hours.dim'; then
    q_dim="$(bashio::config 'quiet_hours.dim')"
    if [[ "${q_dim}" =~ ^(0(\.[0-9]+)?|1(\.0+)?)$ ]]; then
        quiet="$(jq -c --argjson d "${q_dim}" '.dim = $d' <<<"${quiet}")"
    else
        bashio::log.warning "Option 'quiet_hours.dim' is not a number from 0 to 1 ('${q_dim}'); using the default."
    fi
fi

# -------------------------------------------------------------- optional sources
# A remote manifest (a JSON index of images served over HTTP) and a daily image.
# Both off unless the user gives a URL.
static_manifest='{"enabled": false}'
if bashio::config.has_value 'remote_manifest_url'; then
    static_manifest="$(jq -nc --arg u "$(bashio::config 'remote_manifest_url')" \
        '{enabled: true, base_url: $u, id_prefix: "remote"}')"
fi

daily='{"enabled": false}'
if bashio::config.has_value 'daily_json_url' && bashio::config.has_value 'daily_image_url'; then
    daily="$(jq -nc \
        --arg j "$(bashio::config 'daily_json_url')" \
        --arg i "$(bashio::config 'daily_image_url')" \
        '{enabled: true, json_url: $j, image_url: $i, id_prefix: "daily"}')"
elif bashio::config.has_value 'daily_json_url' || bashio::config.has_value 'daily_image_url'; then
    bashio::log.warning "Daily image needs BOTH daily_json_url and daily_image_url; ignoring."
fi

# Open-access art: each source is an option object {enabled, per_day, min_px}, off by default.
art_sources='{}'
art_enabled=0
for source in aic:art_institute_chicago:3 met:the_met:3 potd:wikimedia_picture_of_the_day:1; do
    IFS=: read -r key option per_day_default <<<"${source}"
    on="$(opt "${option}.enabled" false)"
    [[ "${on}" == "true" ]] || on=false
    [[ "${on}" == "false" ]] || art_enabled=$((art_enabled + 1))
    art_sources="$(jq -c --arg k "${key}" --argjson on "${on}" \
        --argjson n "$(int "${option}.per_day" "${per_day_default}")" \
        --argjson px "$(int "${option}.min_px" 2560)" \
        '.[$k] = {enabled: $on, per_day: $n, min_px: $px}' <<<"${art_sources}")"
done
readonly art_enabled

if [[ "${usable_folders}" -eq 0 ]] \
   && [[ "$(jq -r '.enabled' <<<"${static_manifest}")" != "true" ]] \
   && [[ "$(jq -r '.enabled' <<<"${daily}")" != "true" ]] \
   && [[ "${art_enabled}" -eq 0 ]]; then
    bashio::exit.nok \
        "No image source. Add at least one existing folder to 'source_folders' (for example /media/wallpapers or /share/wallpapers), set a remote manifest or daily image URL, or turn on an open-access art source."
fi

# --------------------------------------------------------------------- location
# Day & night rotation changes the picture at sunrise and sunset, so the engine needs to know
# where the house is. Home Assistant knows (homeassistant_api: true). Without it, or with no
# answer, the engine changes at 07:00 and 19:00 instead.
location='null'
if [[ -n "${SUPERVISOR_TOKEN:-}" ]]; then
    for _ in 1 2 3; do
        if core_config="$(curl -fsS --max-time 10 -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
                http://supervisor/core/api/config 2>/dev/null)"; then
            location="$(jq -c 'if (.latitude | type) == "number" and (.longitude | type) == "number"
                               then {latitude, longitude} else null end' <<<"${core_config}" 2>/dev/null)" \
                || location='null'
            break
        fi
        sleep 5
    done
fi
[[ "${location}" != 'null' ]] \
    || bashio::log.info "Home Assistant's location is not known; Day & night changes at 07:00 and 19:00."

# ----------------------------------------------------------------- write config
jq -n \
    --argjson port "${PORT}" \
    --arg data_dir "${DATA_DIR}" \
    --argjson master_max_px "$(int 'master_max_px' 3840)" \
    --argjson cache_max_mb "$(int 'cache_max_mb' 2048)" \
    --argjson render_threads "$(int 'render_threads' 2)" \
    --argjson focus_auto "${focus_auto}" \
    --argjson scan_minutes "$(int 'folder_scan_minutes' 10)" \
    --argjson rot_min "$(int 'rotation_min_minutes' 15)" \
    --argjson rot_max "$(int 'rotation_max_minutes' 20)" \
    --argjson love_weight "$(int 'love_weight' 3)" \
    --argjson quiet_hours "${quiet}" \
    --argjson folders "${folders}" \
    --argjson sets "${sets}" \
    --argjson exclude "${exclude}" \
    --argjson captioners "${captioners}" \
    --argjson static_manifest "${static_manifest}" \
    --argjson daily "${daily}" \
    --argjson art_sources "${art_sources}" \
    --argjson caption_enabled "${captions_on}" \
    --arg caption_language "$(opt 'caption_language' 'English')" \
    --arg caption_spelling "$(opt 'caption_spelling' 'British')" \
    --argjson caption_min_words "$(int 'caption_min_words' 30)" \
    --argjson caption_max_words "$(int 'caption_max_words' 45)" \
    --argjson caption_kids "${kids_on}" \
    --argjson palette_enabled "${palette_on}" \
    --argjson location "${location}" \
    '{
        port: $port,
        data_dir: $data_dir,
        master_max_px: $master_max_px,
        cache_max_mb: $cache_max_mb,
        render_threads: $render_threads,
        focus_auto: $focus_auto,
        folder_scan_minutes: $scan_minutes,
        rotation: {min_minutes: $rot_min, max_minutes: $rot_max, love_weight: $love_weight},
        quiet_hours: $quiet_hours,
        location: $location,
        folders: ($folders | map(. + {sets: $sets})),
        exclude: $exclude,
        captioners: $captioners,
        static_manifest: $static_manifest,
        daily: $daily,
        art_sources: $art_sources,
        caption: {
            enabled: $caption_enabled,
            language: $caption_language,
            spelling: $caption_spelling,
            min_words: $caption_min_words,
            max_words: $caption_max_words,
            kids: {enabled: $caption_kids}
        },
        palette: {enabled: $palette_enabled}
     } | del(..|nulls)' > "${CONFIG_FILE}.tmp"
mv "${CONFIG_FILE}.tmp" "${CONFIG_FILE}"

export WE_CONFIG="${CONFIG_FILE}"
export WE_DATA_DIR="${DATA_DIR}"

bashio::log.info "Matchy Wallpapers: port ${PORT}, ${usable_folders} source folder(s), ${art_enabled} art source(s), captions ${captions_on}, ${captioner_count} captioner(s)."
if [[ "${captions_on}" == "true" ]] && [[ "${captioner_count}" -eq 0 ]]; then
    bashio::log.warning "Captions are on but no captioner is configured: wallpapers will rotate without gallery labels. Add a captioner, or set caption_enabled to false. See DOCS.md."
fi

# The engine needs nothing from the Supervisor (the location was read above), so it does not get
# the token: a bug in an image decoder then cannot reach the Home Assistant API with it.
unset SUPERVISOR_TOKEN
exec python3 /app/engine.py
