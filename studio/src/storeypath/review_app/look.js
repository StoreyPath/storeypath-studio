// How the 3D view is drawn (StoreyPath's world, viewer/src/world/style.js): its look,
// Real or Model, and its quality, Auto, High or Low. Remembered in this browser, for
// Review's 3D and Walk and for Navigate alike (and the 3D page of its own); never needed
// to work: without it, Real and Auto.

const STYLE = "storeypath.world.style", QUALITY = "storeypath.world.quality";

function saved(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function save(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // not remembered; fine
  }
}

/** The look and quality remembered in this browser, as a world's options. */
export function lookOptions() {
  const style = saved(STYLE), quality = saved(QUALITY);
  return { style: style === "model" ? "model" : "real", quality: quality === "high" || quality === "low" ? quality : "auto" };
}

/** The controls of a world's look: ``styles``, buttons (data-style "real" or "model"),
 * and ``quality``, a select (auto, high, low); ``world()`` gives the world when there is
 * one. A click changes it and is remembered; they show what it draws (Auto: the quality
 * it chose). ``attach(world)`` once the world is made. */
export function setupLook({ styles, quality }, world) {
  const show = () => {
    const look = world()?.look ?? { ...lookOptions(), drawn: null };
    for (const b of styles) {
      b.classList.toggle("active", b.dataset.style === look.style);
      b.setAttribute("aria-pressed", String(b.dataset.style === look.style));
    }
    quality.value = look.quality;
    const auto = quality.querySelector("option[value='auto']");
    if (auto) auto.textContent = look.quality === "auto" && look.drawn ? `Auto (${look.drawn === "low" ? "Low" : "High"})` : "Auto";
  };
  for (const b of styles) {
    b.addEventListener("click", () => {
      save(STYLE, b.dataset.style);
      world()?.setStyle(b.dataset.style);
      show();
    });
  }
  quality.addEventListener("change", () => {
    save(QUALITY, quality.value);
    world()?.setQuality(quality.value);
    show();
  });
  show();
  return {
    attach(w) {
      w.addEventListener("lookchange", show);
      show();
    },
  };
}
