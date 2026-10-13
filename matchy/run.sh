#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
#
# Matchy Wallpapers — add-on entrypoint.
#
# Turns add-on options into the engine's config.json, then execs the engine.
# The engine DEEP-MERGES this file over its own defaults, so we write only what
# the user actually set, and never write a JSON null: a null overwrites the
# default and then crashes the engine.
set -euo pipefail

readonly DATA_DIR=/data
readonly CONFIG_FILE="${DATA_DIR}/config.json"
readonly SECRETS_DIR="${DATA_DIR}/secrets"

mkdir -p "${SECRETS_DIR}"
chmod 700 "${SECRETS_DIR}"

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
readonly PORT=8160

# ---------------------------------------------------------------- source folders
folders='[]'
usable_folders=0
if bashio::config.exists 'source_folders'; then
    for idx in $(bashio::config 'source_folders|keys'); do
        folder="$(bashio::config "source_folders[${idx}]")"
        [[ -n "${folder}" ]] || continue
        if [[ ! -d "${folder}" ]]; then
            bashio::log.warn "Source folder not found, skipping: ${folder}"
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
            bashio::log.warn "Captioner ${idx} has no url or no model, skipping."
            continue
        fi
        c_name="$(opt "captioners[${idx}].name" "captioner-${idx}")"
        c_timeout="$(opt "captioners[${idx}].timeout" 120)"
        entry="$(jq -nc \
            --arg n "${c_name}" --arg u "${c_url}" --arg m "${c_model}" \
            --argjson t "${c_timeout}" \
            '{name: $n, url: $u, model: $m, timeout: $t}')"

        # The key goes to a 600 file inside /data; only its path enters the config,
        # so the key is never in config.json and never in the log.
        if bashio::config.has_value "captioners[${idx}].api_key"; then
            safe_name="$(printf '%s' "${c_name}" | tr -cs 'A-Za-z0-9_.-' '_')"
            key_file="${SECRETS_DIR}/${safe_name}.key"
            ( umask 077; bashio::config "captioners[${idx}].api_key" > "${key_file}" )
            chmod 600 "${key_file}"
            entry="$(jq -c --arg f "${key_file}" '.api_key_file = $f' <<<"${entry}")"
        fi

        captioners="$(jq -c --argjson e "${entry}" '. + [$e]' <<<"${captioners}")"
    done
fi
readonly captioner_count="$(jq 'length' <<<"${captioners}")"

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
    bashio::log.warn "Daily image needs BOTH daily_json_url and daily_image_url; ignoring."
fi

if [[ "${usable_folders}" -eq 0 ]] \
   && [[ "$(jq -r '.enabled' <<<"${static_manifest}")" != "true" ]] \
   && [[ "$(jq -r '.enabled' <<<"${daily}")" != "true" ]]; then
    bashio::exit.nok \
        "No image source. Add at least one existing folder to 'source_folders' (for example /media/wallpapers or /share/wallpapers), or set a remote manifest or daily image URL."
fi

# ----------------------------------------------------------------- write config
jq -n \
    --argjson port "${PORT}" \
    --arg data_dir "${DATA_DIR}" \
    --argjson master_max_px "$(opt 'master_max_px' 3840)" \
    --argjson cache_max_mb "$(opt 'cache_max_mb' 2048)" \
    --argjson render_threads "$(opt 'render_threads' 2)" \
    --argjson scan_minutes "$(opt 'folder_scan_minutes' 10)" \
    --argjson rot_min "$(opt 'rotation_min_minutes' 15)" \
    --argjson rot_max "$(opt 'rotation_max_minutes' 20)" \
    --argjson folders "${folders}" \
    --argjson captioners "${captioners}" \
    --argjson static_manifest "${static_manifest}" \
    --argjson daily "${daily}" \
    --argjson caption_enabled "$(bashio::config.true 'caption_enabled' && echo true || echo false)" \
    --arg caption_language "$(opt 'caption_language' 'English')" \
    --arg caption_spelling "$(opt 'caption_spelling' 'British')" \
    --argjson caption_min_words "$(opt 'caption_min_words' 30)" \
    --argjson caption_max_words "$(opt 'caption_max_words' 45)" \
    '{
        port: $port,
        data_dir: $data_dir,
        master_max_px: $master_max_px,
        cache_max_mb: $cache_max_mb,
        render_threads: $render_threads,
        folder_scan_minutes: $scan_minutes,
        rotation: {min_minutes: $rot_min, max_minutes: $rot_max},
        folders: $folders,
        captioners: $captioners,
        static_manifest: $static_manifest,
        daily: $daily,
        caption: {
            enabled: $caption_enabled,
            language: $caption_language,
            spelling: $caption_spelling,
            min_words: $caption_min_words,
            max_words: $caption_max_words
        }
     } | del(..|nulls)' > "${CONFIG_FILE}"

export WE_CONFIG="${CONFIG_FILE}"
export WE_DATA_DIR="${DATA_DIR}"

bashio::log.info "Matchy Wallpapers: port ${PORT}, ${usable_folders} source folder(s), captions $(bashio::config.true 'caption_enabled' && echo on || echo off), ${captioner_count} captioner(s)."
if [[ "${captioner_count}" -eq 0 ]]; then
    bashio::log.info "No captioner configured: images will rotate without gallery labels."
fi

exec python3 /app/engine.py
