/*
 * CV Builder dashboard.
 *
 * The whole editor is driven by one `state.doc` object that mirrors the server's
 * CVDocument model. Text inputs are bound directly to the object they edit, so
 * typing never re-renders the form (which would drop focus mid-word). Only
 * structural edits - add, remove, reorder, change kind - trigger a re-render.
 */

"use strict";

/*
 * Where the API lives, and who it belongs to.
 *
 * Standalone, cv-builder serves this page at "/" and its API at "/api", with one
 * store for the whole process. Mounted inside job-scout's dashboard the same
 * router answers under "/api/cv" - the page is told so through
 * window.CV_API_BASE - and that API is multi-user, so every call has to name the
 * user this page was opened for (/cv/?user=<name>). Both default back to the
 * standalone behaviour, so this one file serves either host.
 */
const API_BASE = window.CV_API_BASE || "/api";
const PAGE_USER = new URLSearchParams(window.location.search).get("user") || "";

/* window.CV_API_USER is read per call, so a host page that embeds this editor
 * can switch user without reloading it; otherwise the user is the one this page
 * was opened for. Standalone both are empty and no user is ever sent. */
function apiUser() {
  return window.CV_API_USER || PAGE_USER;
}

function apiUrl(path) {
  const url = `${API_BASE}${path}`;
  const user = apiUser();
  if (!user) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}user=${encodeURIComponent(user)}`;
}

/*
 * The dashboard keeps its token in sessionStorage and guards everything under
 * /api/, this router included. Same origin, so reuse it rather than asking
 * again; standalone there is no token and this stays empty.
 */
function authToken() {
  try {
    return sessionStorage.getItem("dashboardToken") || "";
  } catch (error) {
    return ""; /* storage blocked: the API is either open, or will answer 401 */
  }
}

const state = {
  slug: null,
  doc: null,
  meta: null,
  previewUrl: null,
  saving: false,
};

const SECTION_LABELS = {
  text: "Free text",
  experience: "Experience",
  education: "Education",
  skills: "Skills",
  details: "Details table",
  contact: "Contact",
  list: "List",
};

/* ------------------------------------------------------------------ */
/* DOM helpers                                                         */
/* ------------------------------------------------------------------ */

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

function labelled(text, control) {
  return el("label", { class: "field" }, [el("span", { text }), control]);
}

/** Bind an input to `object[key]`, updating the model on every keystroke. */
function bind(object, key, { type = "text", placeholder = "", rows = 0 } = {}) {
  const node =
    rows > 0
      ? el("textarea", { rows, placeholder })
      : el("input", { type, placeholder });
  node.value = object[key] ?? "";
  node.addEventListener("input", () => {
    object[key] = node.value;
    markDirty();
  });
  return node;
}

/**
 * A slider paired with a number box, each editable and kept in sync.
 *
 * The slider alone is fine for eyeballing but useless for "make it exactly 8.6",
 * so both controls write to the model and echo into the other.
 */
function bindNumber(object, key, { min, max, step }) {
  const slider = el("input", { type: "range", min, max, step });
  // step="any" on the box: the slider snaps to `step`, but a typed 8.6 must not
  // be rejected by the browser just because it falls between two slider stops.
  const box = el("input", { type: "number", min, max, step: "any" });
  slider.value = object[key];
  box.value = object[key];

  const apply = (raw, echo) => {
    const value = parseFloat(raw);
    // Ignore half-typed input such as "" or "8." rather than snapping the value
    // out from under the cursor.
    if (Number.isNaN(value)) return;
    object[key] = Math.min(max, Math.max(min, value));
    echo.value = object[key];
    markDirty();
  };

  slider.addEventListener("input", () => apply(slider.value, box));
  box.addEventListener("input", () => apply(box.value, slider));
  // On commit, show what the model actually holds, so an out-of-range entry
  // visibly snaps back to the clamped value.
  box.addEventListener("change", () => {
    box.value = object[key];
  });
  return el("div", { class: "slider-row" }, [slider, box]);
}

function bindCheckbox(object, key, text) {
  const box = el("input", { type: "checkbox" });
  box.checked = Boolean(object[key]);
  box.addEventListener("change", () => {
    object[key] = box.checked;
    markDirty();
    renderSections();
  });
  return el("label", { class: "checkbox" }, [box, el("span", { text })]);
}

function bindSelect(object, key, options, onChange) {
  const node = el("select");
  for (const option of options) {
    const value = typeof option === "string" ? option : option.value;
    const text = typeof option === "string" ? option : option.label;
    node.append(el("option", { value, text }));
  }
  node.value = object[key] ?? "";
  node.addEventListener("change", () => {
    object[key] = node.value;
    markDirty();
    if (onChange) onChange(node.value);
  });
  return node;
}

/** Expand `#abc` to `#aabbcc`; <input type="color"> only accepts 6 digits. */
function expandHex(value) {
  const raw = value.trim().replace(/^#/, "");
  if (raw.length !== 3) return `#${raw}`.toUpperCase();
  return `#${raw[0]}${raw[0]}${raw[1]}${raw[1]}${raw[2]}${raw[2]}`.toUpperCase();
}

/**
 * A colour swatch paired with an editable hex field.
 *
 * The native picker cannot be typed into, which makes it impossible to paste a
 * brand colour or copy one between fields.
 */
function bindColour(object, key, text) {
  const swatch = el("input", { type: "color" });
  const code = el("input", {
    type: "text",
    placeholder: "#0B3D2C",
    maxlength: 7,
    spellcheck: "false",
  });
  swatch.value = expandHex(object[key]);
  code.value = object[key];

  swatch.addEventListener("input", () => {
    object[key] = swatch.value.toUpperCase();
    code.value = object[key];
    code.classList.remove("invalid");
    markDirty();
  });

  code.addEventListener("input", () => {
    const value = code.value.trim();
    if (!/^#?(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(value)) {
      // Flag it but keep the keystrokes; the model holds the last good value.
      code.classList.add("invalid");
      return;
    }
    code.classList.remove("invalid");
    const full = expandHex(value);
    swatch.value = full;
    object[key] = full;
    markDirty();
  });

  code.addEventListener("change", () => {
    code.value = object[key];
    code.classList.remove("invalid");
  });

  return el("label", { class: "field" }, [
    el("span", { text }),
    el("div", { class: "colour-row" }, [swatch, code]),
  ]);
}

function iconSelect(object, key = "icon", { allowNone = false } = {}) {
  const options = allowNone ? [{ value: "", label: "(no icon)" }] : [];
  for (const name of state.meta.icons) options.push({ value: name, label: name });
  const node = bindSelect(object, key, options);
  if (object[key] == null) node.value = "";
  node.addEventListener("change", () => {
    object[key] = node.value || (allowNone ? null : node.value);
    markDirty();
  });
  return node;
}

function rowButtons(list, index, onChange) {
  const move = (delta) => {
    const target = index + delta;
    if (target < 0 || target >= list.length) return;
    [list[index], list[target]] = [list[target], list[index]];
    onChange();
  };
  return el("div", { class: "row-actions" }, [
    el("button", {
      class: "icon-btn",
      type: "button",
      title: "Move up",
      onclick: () => move(-1),
      disabled: index === 0,
    }, "↑"),
    el("button", {
      class: "icon-btn",
      type: "button",
      title: "Move down",
      onclick: () => move(1),
      disabled: index === list.length - 1,
    }, "↓"),
    el("button", {
      class: "icon-btn danger",
      type: "button",
      title: "Remove",
      onclick: () => {
        list.splice(index, 1);
        onChange();
      },
    }, "✕"),
  ]);
}

/* ------------------------------------------------------------------ */
/* API                                                                 */
/* ------------------------------------------------------------------ */

/**
 * Turn FastAPI's 422 payload into something a human can act on.
 *
 * Pydantic returns a list of {loc, msg} objects; dumped as raw JSON it is a wall
 * of noise. This renders one "where: what" line per problem instead.
 */
function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return JSON.stringify(detail, null, 2);
  return detail
    .map((item) => {
      const where = (item.loc || [])
        .filter((part) => part !== "body")
        .join(" → ");
      const message = item.msg || JSON.stringify(item);
      return where ? `${where}: ${message}` : message;
    })
    .join("\n");
}

async function request(url, options = {}) {
  const token = authToken();
  const headers = token
    ? { ...(options.headers || {}), Authorization: `Bearer ${token}` }
    : options.headers;
  const response = await fetch(url, { ...options, headers });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) detail = formatDetail(body.detail);
    } catch (error) {
      /* response had no JSON body; keep the status line */
    }
    throw new Error(detail);
  }
  return response;
}

const api = {
  meta: () => request(apiUrl("/meta")).then((r) => r.json()),
  list: () => request(apiUrl("/profiles")).then((r) => r.json()),
  get: (slug) => request(apiUrl(`/profiles/${slug}`)).then((r) => r.json()),
  save: (slug, doc) =>
    request(apiUrl(`/profiles/${slug}`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(doc),
    }).then((r) => r.json()),
  create: (name) =>
    request(apiUrl("/profiles"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).then((r) => r.json()),
  importOptions: () => request(apiUrl("/import/options")).then((r) => r.json()),
  importCv: (form) =>
    request(apiUrl("/import"), { method: "POST", body: form }).then((r) => r.json()),
  remove: (slug) => request(apiUrl(`/profiles/${slug}`), { method: "DELETE" }),
  uploadPhoto: (slug, file) => {
    const form = new FormData();
    form.append("file", file);
    return request(apiUrl(`/profiles/${slug}/photo`), {
      method: "POST",
      body: form,
    }).then((r) => r.json());
  },
  deletePhoto: (slug) =>
    request(apiUrl(`/profiles/${slug}/photo`), { method: "DELETE" }),
  preview: (slug, doc) =>
    request(apiUrl(`/profiles/${slug}/preview`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(doc),
    }).then((r) => r.blob()),
  photo: (slug) =>
    request(apiUrl(`/profiles/${slug}/photo?v=${Date.now()}`)).then((r) =>
      r.blob()
    ),
  pdf: (slug) => request(apiUrl(`/profiles/${slug}/pdf`)),
};

/* ------------------------------------------------------------------ */
/* Status and preview                                                  */
/* ------------------------------------------------------------------ */

const statusNode = () => document.getElementById("status");

function setStatus(text, isError = false) {
  const node = statusNode();
  node.textContent = text;
  node.classList.toggle("error", isError);
}

let previewTimer = null;
let previewSeq = 0;

function markDirty() {
  setStatus("Unsaved changes");
  if (document.getElementById("auto-preview").checked) schedulePreview();
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshPreview, 650);
}

async function refreshPreview() {
  if (!state.slug || !state.doc) return;
  const errorNode = document.getElementById("preview-error");

  // Renders take variable time, so two in flight can finish out of order. Only
  // the newest request is allowed to touch the iframe; stale ones bail out
  // before allocating a blob URL, so nothing leaks either.
  const seq = ++previewSeq;
  try {
    const blob = await api.preview(state.slug, state.doc);
    if (seq !== previewSeq) return;
    const url = URL.createObjectURL(blob);
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = url;
    // #toolbar=0 keeps the built-in PDF chrome out of the way in Chrome/Edge.
    document.getElementById("preview").src = `${url}#toolbar=0`;
    errorNode.hidden = true;
  } catch (error) {
    if (seq !== previewSeq) return;
    errorNode.hidden = false;
    errorNode.textContent = `Preview failed:\n\n${error.message}`;
  }
}

/* ------------------------------------------------------------------ */
/* Identity and theme panels                                           */
/* ------------------------------------------------------------------ */

const BLANK_PORTRAIT =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E";

/*
 * Fetch the portrait instead of linking it. An <img src> cannot carry the
 * dashboard's Authorization header, and mounted under job-scout the portrait
 * sits behind it; a blob URL sidesteps that and works standalone too.
 */
async function showPortrait(image, slug) {
  try {
    const url = URL.createObjectURL(await api.photo(slug));
    image.addEventListener("load", () => URL.revokeObjectURL(url), {
      once: true,
    });
    image.src = url;
  } catch (error) {
    setStatus(error.message, true);
  }
}

function renderIdentity() {
  const panel = document.getElementById("identity-panel");
  panel.replaceChildren();
  const doc = state.doc;

  panel.append(
    labelled("Full name", bind(doc, "full_name", { placeholder: "Lorem Ipsum" })),
    labelled(
      "Headline (optional)",
      bind(doc, "headline", { placeholder: "Lorem ipsum dolor sit amet" })
    ),
    labelled(
      "Language tag (used in the PDF filename)",
      bind(doc, "language", { placeholder: "EN" })
    )
  );

  const image = el("img", {
    class: "photo-preview",
    alt: "Portrait preview",
    src: BLANK_PORTRAIT,
  });
  if (doc.photo) showPortrait(image, state.slug);

  const picker = el("input", { type: "file", accept: "image/*" });
  picker.addEventListener("change", async () => {
    const file = picker.files[0];
    if (!file) return;
    try {
      setStatus("Uploading…");
      const result = await api.uploadPhoto(state.slug, file);
      doc.photo = result.photo;
      renderIdentity();
      setStatus("Portrait updated");
      refreshPreview();
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  const remove = el("button", {
    class: "icon-btn danger",
    type: "button",
    onclick: async () => {
      try {
        await api.deletePhoto(state.slug);
        doc.photo = "";
        renderIdentity();
        refreshPreview();
      } catch (error) {
        setStatus(error.message, true);
      }
    },
    disabled: !doc.photo,
  }, "Remove");

  panel.append(
    el("div", { class: "photo-row" }, [
      image,
      el("div", { class: "field" }, [
        el("span", { text: "Portrait" }),
        picker,
      ]),
      remove,
    ])
  );
}

function renderTheme() {
  const panel = document.getElementById("theme-panel");
  panel.replaceChildren();
  const theme = state.doc.theme;

  panel.append(
    el("div", { class: "grid three" }, [
      bindColour(theme, "sidebar_bg", "Sidebar"),
      bindColour(theme, "accent", "Accent bars"),
      bindColour(theme, "page_bg", "Page"),
      bindColour(theme, "sidebar_text", "Sidebar text"),
      bindColour(theme, "body_text", "Body text"),
      bindColour(theme, "heading_text", "Headings"),
    ]),
    el("div", { class: "grid two" }, [
      labelled("Font", bindSelect(theme, "font_family", ["Lato", "Helvetica"])),
      labelled("Page size", bindSelect(theme, "page_size", ["A4", "LETTER"])),
    ]),
    labelled(
      "Base font size (pt)",
      bindNumber(theme, "base_font_size", { min: 6, max: 14, step: 0.25 })
    ),
    labelled(
      "Line spacing",
      bindNumber(theme, "line_spacing", { min: 1, max: 2, step: 0.02 })
    ),
    labelled(
      "Sidebar width",
      bindNumber(theme, "sidebar_width_pct", { min: 0.2, max: 0.55, step: 0.005 })
    ),
    labelled(
      "Portrait size",
      bindNumber(theme, "photo_diameter_pct", { min: 0.3, max: 1, step: 0.01 })
    ),
    el("div", { class: "grid two" }, [
      bindCheckbox(theme, "show_photo", "Show portrait"),
      bindCheckbox(theme, "uppercase_headings", "Uppercase headings"),
    ])
  );
}

/* ------------------------------------------------------------------ */
/* Section editors                                                     */
/* ------------------------------------------------------------------ */

function newSection(kind) {
  const base = { title: "New section", icon: null, enabled: true, kind };
  const extras = {
    text: { body: "" },
    experience: {
      entries: [],
      description_label: "Job description:",
    },
    education: {
      entries: [],
      school_label: "School:",
      period_label: "Period:",
      courses_label: "Main courses:",
    },
    skills: { items: [], columns: 2, show_levels: false },
    details: { items: [], label_suffix: ":" },
    contact: { items: [] },
    list: { items: [], bulleted: false },
  };
  return { ...base, ...extras[kind] };
}

function entryCard(title, list, index, fields, onChange) {
  return el("div", { class: "entry" }, [
    el("div", { class: "entry-head" }, [
      el("strong", { text: `${title} ${index + 1}` }),
      rowButtons(list, index, onChange),
    ]),
    ...fields,
  ]);
}

function addButton(text, onclick) {
  return el("button", { class: "add-row", type: "button", onclick }, text);
}

const EDITORS = {
  text(section) {
    return [
      labelled(
        "Body",
        bind(section, "body", {
          rows: 5,
          placeholder: "Lorem ipsum dolor sit amet, consectetur adipiscing elit…",
        })
      ),
    ];
  },

  experience(section, rerender) {
    const nodes = section.entries.map((entry, index) =>
      entryCard("Role", section.entries, index, [
        el("div", { class: "grid two" }, [
          labelled("Job title", bind(entry, "title")),
          labelled("Organisation", bind(entry, "organisation")),
        ]),
        labelled("Period", bind(entry, "period", { placeholder: "Mar 2021 - present" })),
        labelled("Description", bind(entry, "description", { rows: 4 })),
        labelled(
          "Bullet points (one per line)",
          bulletEditor(entry, "bullets")
        ),
      ], rerender)
    );
    return [
      labelled("Description label", bind(section, "description_label")),
      ...nodes,
      addButton("+ Add role", () => {
        section.entries.push({
          title: "",
          organisation: "",
          period: "",
          description: "",
          bullets: [],
        });
        rerender();
      }),
    ];
  },

  education(section, rerender) {
    const nodes = section.entries.map((entry, index) =>
      entryCard("Qualification", section.entries, index, [
        labelled("Degree", bind(entry, "degree")),
        el("div", { class: "grid two" }, [
          labelled("School", bind(entry, "school")),
          labelled("Period", bind(entry, "period")),
        ]),
        labelled("Main courses", bind(entry, "courses", { rows: 3 })),
        labelled("Note (optional)", bind(entry, "note")),
      ], rerender)
    );
    return [
      el("div", { class: "grid three" }, [
        labelled("School label", bind(section, "school_label")),
        labelled("Period label", bind(section, "period_label")),
        labelled("Courses label", bind(section, "courses_label")),
      ]),
      el("p", { class: "hint" }, "Blank a label to hide its prefix."),
      ...nodes,
      addButton("+ Add qualification", () => {
        section.entries.push({
          degree: "",
          school: "",
          period: "",
          courses: "",
          note: "",
        });
        rerender();
      }),
    ];
  },

  skills(section, rerender) {
    const rows = section.items.map((item, index) =>
      el("div", { class: "grid two" }, [
        bind(item, "name", { placeholder: "Lorem ipsum" }),
        el("div", { class: "row-actions" }, [
          section.show_levels ? levelInput(item) : null,
          rowButtons(section.items, index, rerender),
        ]),
      ])
    );
    return [
      el("div", { class: "grid two" }, [
        labelled(
          "Columns",
          bindSelect(section, "columns", [
            { value: 1, label: "1" },
            { value: 2, label: "2" },
            { value: 3, label: "3" },
          ], () => {
            section.columns = parseInt(section.columns, 10);
            rerender();
          })
        ),
        bindCheckbox(section, "show_levels", "Show rating bars"),
      ]),
      ...rows,
      addButton("+ Add skill", () => {
        section.items.push({ name: "", level: null });
        rerender();
      }),
    ];
  },

  details(section, rerender) {
    const rows = section.items.map((item, index) =>
      el("div", { class: "grid two" }, [
        bind(item, "label", { placeholder: "Lorem" }),
        el("div", { class: "row-actions" }, [
          bind(item, "value", { placeholder: "Ipsum dolor" }),
          rowButtons(section.items, index, rerender),
        ]),
      ])
    );
    return [
      labelled("Label suffix", bind(section, "label_suffix")),
      ...rows,
      addButton("+ Add row", () => {
        section.items.push({ label: "", value: "" });
        rerender();
      }),
    ];
  },

  contact(section, rerender) {
    const rows = section.items.map((item, index) =>
      el("div", { class: "entry" }, [
        el("div", { class: "entry-head" }, [
          iconSelect(item),
          rowButtons(section.items, index, rerender),
        ]),
        labelled("Text", bind(item, "value", { placeholder: "lorem@ipsum.com" })),
        labelled("Link (optional)", bind(item, "url", { placeholder: "https://…" })),
      ])
    );
    return [
      ...rows,
      addButton("+ Add contact line", () => {
        section.items.push({ icon: "envelope", value: "", url: "" });
        rerender();
      }),
    ];
  },

  list(section, rerender) {
    return [
      bindCheckbox(section, "bulleted", "Show bullets"),
      labelled("Items (one per line)", bulletEditor(section, "items")),
    ];
  },
};

/** Edit a string array as one item per line of a textarea. */
function bulletEditor(object, key) {
  const node = el("textarea", { rows: 3, placeholder: "One item per line" });
  node.value = (object[key] || []).join("\n");
  node.addEventListener("input", () => {
    object[key] = node.value
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    markDirty();
  });
  return node;
}

function levelInput(item) {
  const node = el("input", { type: "number", min: 0, max: 5, step: 1 });
  node.value = item.level ?? "";
  node.addEventListener("input", () => {
    item.level = node.value === "" ? null : parseInt(node.value, 10);
    markDirty();
  });
  return node;
}

function renderSection(section, list, index, column) {
  const rerender = () => {
    renderSections();
    markDirty();
  };

  const kindSelect = bindSelect(
    section,
    "kind",
    Object.entries(SECTION_LABELS).map(([value, label]) => ({ value, label })),
    (kind) => {
      list[index] = { ...newSection(kind), title: section.title, icon: section.icon };
      rerender();
    }
  );

  const head = el("div", { class: "section-head" }, [
    bind(section, "title", { placeholder: "Section title" }),
    kindSelect,
    iconSelect(section, "icon", { allowNone: true }),
    bindCheckbox(section, "enabled", "On"),
    rowButtons(list, index, rerender),
  ]);

  const body = el(
    "div",
    { class: "section-body" },
    EDITORS[section.kind](section, rerender)
  );

  return el(
    "div",
    { class: `section-card${section.enabled ? "" : " disabled"}` },
    [head, body]
  );
}

function renderSections() {
  for (const column of ["sidebar", "main"]) {
    const host = document.getElementById(`${column}-sections`);
    host.replaceChildren();
    const list = state.doc[column];
    list.forEach((section, index) => {
      host.append(renderSection(section, list, index, column));
    });
  }
}

function renderAll() {
  renderIdentity();
  renderTheme();
  renderSections();
}

/* ------------------------------------------------------------------ */
/* Profiles                                                            */
/* ------------------------------------------------------------------ */

async function loadProfile(slug) {
  state.slug = slug;
  state.doc = await api.get(slug);
  renderAll();
  setStatus("Loaded");
  refreshPreview();
}

async function refreshProfileList(selected) {
  const profiles = await api.list();
  const picker = document.getElementById("profile-picker");
  picker.replaceChildren();
  for (const profile of profiles) {
    picker.append(
      el("option", {
        value: profile.slug,
        text: profile.full_name
          ? `${profile.full_name} (${profile.slug})`
          : `${profile.slug} (empty)`,
      })
    );
  }
  const target = selected || profiles[0]?.slug;
  if (target) {
    picker.value = target;
    await loadProfile(target);
  }
  document.getElementById("delete-profile").disabled = profiles.length <= 1;
}

async function save() {
  if (state.saving) return;
  state.saving = true;
  try {
    setStatus("Saving…");
    await api.save(state.slug, state.doc);
    setStatus("Saved");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    state.saving = false;
  }
}

/* ------------------------------------------------------------------ */
/* Wiring                                                              */
/* ------------------------------------------------------------------ */

function wire() {
  document.getElementById("profile-picker").addEventListener("change", (event) => {
    loadProfile(event.target.value).catch((error) =>
      setStatus(error.message, true)
    );
  });

  document.getElementById("new-profile").addEventListener("click", async () => {
    const name = prompt("Name for the new CV profile:");
    if (!name) return;
    try {
      const created = await api.create(name);
      await refreshProfileList(created.slug);
      setStatus("Profile created");
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  document.getElementById("delete-profile").addEventListener("click", async () => {
    if (!confirm(`Delete profile "${state.slug}" and its uploads?`)) return;
    try {
      await api.remove(state.slug);
      await refreshProfileList();
      setStatus("Profile deleted");
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  document.getElementById("save").addEventListener("click", save);
  document.getElementById("refresh").addEventListener("click", refreshPreview);

  document.getElementById("download").addEventListener("click", async () => {
    await save();
    try {
      /* Fetched for the same reason as the portrait: a plain navigation cannot
       * send the dashboard's token. The server still names the file. */
      const response = await api.pdf(state.slug);
      const disposition = response.headers.get("content-disposition") || "";
      const named = /filename="([^"]+)"/.exec(disposition);
      const url = URL.createObjectURL(await response.blob());
      const link = el("a", { href: url, download: named ? named[1] : "cv.pdf" });
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  for (const button of document.querySelectorAll(".add-section")) {
    button.addEventListener("click", () => {
      state.doc[button.dataset.column].push(newSection("text"));
      renderSections();
      markDirty();
    });
  }

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "s") {
      event.preventDefault();
      save();
    }
  });
}

/* ------------------------------------------------------------------ */
/* Importing an existing CV                                            */
/* ------------------------------------------------------------------ */

/*
 * Import turns a CV the user already has - the file they gave job-scout in
 * Profile & Filters, or one they upload here - into a profile they can edit. It needs
 * the host's LLM settings, so only job-scout's dashboard provides it: the
 * button stays hidden when /import/options is not there (standalone).
 */
let importOptions = null;

async function setupImport() {
  try {
    importOptions = await api.importOptions();
  } catch (error) {
    return; /* no import endpoint on this host */
  }
  const button = document.getElementById("import-profile");
  button.hidden = false;
  button.addEventListener("click", openImportDialog);
}

function importDialog() {
  const settingsName = importOptions.settings_cv;
  const source = el("select", { name: "source" }, [
    settingsName
      ? el("option", { value: "settings", text: `My uploaded CV (${settingsName})` })
      : null,
    el("option", { value: "upload", text: "Upload a file (PDF, DOCX, ODT or text)" }),
  ]);
  const file = el("input", { type: "file", name: "file", accept: ".pdf,.docx,.odt,.txt" });
  const fileField = labelled("File", file);
  const toggleFile = () => {
    fileField.hidden = source.value !== "upload";
  };
  source.addEventListener("change", toggleFile);
  toggleFile();
  const language = el("select", { name: "language" }, [
    el("option", { value: "NL", text: "Nederlands" }),
    el("option", { value: "EN", text: "English" }),
  ]);
  const name = el("input", { name: "name", required: true, value: "mijn-cv" });
  const note = el("p", {
    class: "import-note",
    text:
      "Your CV is read and laid out as an editable profile. Nothing is added " +
      "that is not in it" +
      (importOptions.linkedin
        ? ", apart from roles your parsed profile or LinkedIn import adds"
        : "") +
      ". Check the result before you use it.",
  });
  const status = el("p", { class: "import-status", role: "status" });
  const submit = el("button", { class: "primary solid", type: "submit", text: "Import" });
  const cancel = el("button", { class: "ghost", type: "button", text: "Cancel" });
  const form = el("form", { method: "dialog", class: "import-form" }, [
    el("h2", { text: "Import your CV" }),
    labelled("Take the CV from", source),
    fileField,
    labelled("Profile language", language),
    labelled("Profile name", name),
    note,
    status,
    el("div", { class: "import-actions" }, [cancel, submit]),
  ]);
  const dialog = el("dialog", { class: "import-dialog" }, [form]);
  cancel.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => dialog.remove());
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    runImport({ dialog, source, file, language, name, status, submit });
  });
  return dialog;
}

function openImportDialog() {
  const dialog = importDialog();
  document.body.append(dialog);
  dialog.showModal();
}

async function runImport({ dialog, source, file, language, name, status, submit }) {
  const form = new FormData();
  form.append("source", source.value);
  form.append("language", language.value);
  form.append("name", name.value.trim());
  if (source.value === "upload") {
    if (!file.files.length) {
      status.textContent = "Choose a file to upload.";
      return;
    }
    form.append("file", file.files[0]);
  }
  submit.disabled = true;
  status.textContent = "Reading your CV… this can take a minute or two.";
  try {
    const result = await api.importCv(form);
    dialog.close();
    await refreshProfileList(result.slug);
    const checks = result.warnings.length ? ` Check: ${result.warnings.join(" ")}` : "";
    setStatus(`Imported from ${result.sources_used.join(", ")}.${checks}`, false);
  } catch (error) {
    status.textContent = error.message;
    submit.disabled = false;
  }
}

async function boot() {
  try {
    state.meta = await api.meta();
    wire();
    setupImport();
    await refreshProfileList();
  } catch (error) {
    setStatus(error.message, true);
  }
}

boot();
