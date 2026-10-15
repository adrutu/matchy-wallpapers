# Homepage dashboard background

[Homepage](https://gethomepage.dev) paints its background once from `settings.yaml`; a few lines of
`custom.js` then swap the image on every rotation without a page reload, and a tile shows what is on
screen. Replace `<matchy-url>` below with where your browser can reach Matchy, for example
`http://<your-ha-host>:<port>` (the host port you published for the add-on).

**Mixed content:** if Homepage itself is served over `https://`, the browser refuses an `http://`
background. Put Matchy behind the same reverse proxy with TLS and use that `https://` name here.

## 1. `settings.yaml`: the first paint

```yaml
# Matchy: first paint only; custom.js swaps the image on every rotation.
background:
  image: <matchy-url>/current/clean/2560x1440.jpg
  brightness: 75
cardBlur: md
```

`/current/clean/...` always answers with whatever is on screen now, so a fresh page load is right
from the first frame. After editing `settings.yaml`, call Homepage's revalidate endpoint once, or a
browser that has never seen the page may get the stale build (flat colour, no wallpaper):

```bash
curl -s http://<homepage-host>:3000/api/revalidate      # {"revalidated":true}
```

## 2. `custom.js`: follow the rotation

```js
/* Matchy: keep Homepage's #background on the engine's current picture.
   settings.yaml background.image only paints the first frame; this swaps the url() on every
   rotation (polls current.json once a minute, preloads the new image, then swaps). */
(function () {
  var BASE = "<matchy-url>";
  var shown = null;

  // A few fixed sizes keep the engine's render cache warm.
  function size() {
    var w = window.screen.width, h = window.screen.height;
    var r = w / h;
    if (r < 1) return [1200, 1920];
    if (r > 2) return [3440, 1440];
    if (r < 1.7) return [2560, 1600];
    return [2560, 1440];
  }

  function swap(url, version) {
    var bg = document.getElementById("background");
    if (!bg) return;
    bg.style.backgroundImage = bg.style.backgroundImage.replace(/url\((['"]?).*?\1\)/, 'url("' + url + '")');
    shown = version;
  }

  function tick() {
    fetch(BASE + "/current.json", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (cur) {
        if (!cur || cur.version === shown) return;
        var s = size();
        var url = BASE + cur.urls.clean.replace("{w}", s[0]).replace("{h}", s[1]);
        var img = new Image();
        img.onload = function () { swap(url, cur.version); };
        img.src = url;
      })
      .catch(function () {});
  }

  setTimeout(tick, 1500);
  setInterval(tick, 60000);
})();
```

`current.json` is served with `Access-Control-Allow-Origin: *`, so the browser may read it from the
Homepage origin. The clean image is used on purpose: Homepage's cards cover the corner where the
label sits.

## 3. `custom.css`: keep the cards readable (optional)

A photo behind translucent cards can swallow the text. A darker card tint plus a shadow on the text
that sits straight on the picture fixes it; change the alpha to taste.

```css
li.service > div {
  background-color: rgba(8, 12, 10, 0.75) !important;
  border: 1px solid rgba(255, 255, 255, 0.08) !important;
}
li.service > div:hover {
  background-color: rgba(8, 12, 10, 0.85) !important;
}
.service-group-name,
.bookmark-group-name,
li.bookmark a,
.information-widgets {
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.9), 0 0 12px rgba(0, 0, 0, 0.6);
}
```

## 4. `services.yaml`: a tile

```yaml
- Photos:
    - Matchy:
        href: <matchy-url>
        icon: mdi-image-frame
        description: Rotating wallpaper · gallery labels
        siteMonitor: <matchy-url>/healthz
        widget:
          type: customapi
          url: <matchy-url>/current.json
          refreshInterval: 60000
          display: list
          mappings:
            - field: title
              label: Now showing
            - field: next_change
              label: Next change
              format: relativeDate
```

`siteMonitor` and the `customapi` widget are fetched by the Homepage container, not the browser, so
they need a URL Homepage can reach; the `href` and the background are opened by the browser.
