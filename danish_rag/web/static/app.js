const evidenceReturnTargets = new WeakMap();
const requestStatusByFormClass = new Map([
  ["composer", "Preparing answer."],
  ["setup-form", "Testing provider."],
  ["model-discovery-button", "Finding compatible local models."],
]);
const swapStatusByTargetId = new Map([
  ["conversation-main", "Conversation updated."],
  ["setup-panel", "Provider setup updated."],
]);

function announceStatus(message) {
  const status = document.getElementById("interaction-status");
  if (!status) {
    return;
  }

  status.textContent = "";
  window.setTimeout(() => {
    status.textContent = message;
  }, 0);
}

function focusConversationTitle(root = document, { preventScroll = false } = {}) {
  const title = root.querySelector("#conversation-title");
  if (title instanceof HTMLElement) {
    title.focus({ preventScroll });
  }
}

function revealLatestTurn(conversation) {
  if (conversation.dataset.revealLatestTurn !== "true") {
    return;
  }
  const turnStack = conversation.querySelector(".turn-stack");
  if (turnStack instanceof HTMLElement) {
    turnStack.scrollTop = turnStack.scrollHeight;
  }
}

// The home intro is a scroller only when it overflows; make it a keyboard stop
// exactly then (axe scrollable-region-focusable) and never when it cannot scroll.
// A focused intro keeps its tabindex until it loses focus so focus never drops to body.
let introObserver;

function syncIntroFocus(intro) {
  if (intro.scrollHeight > intro.clientHeight) {
    intro.setAttribute("tabindex", "0");
  } else if (document.activeElement !== intro) {
    intro.removeAttribute("tabindex");
  }
}

function syncCurrentIntro() {
  const current = document.querySelector(".empty-state");
  if (current instanceof HTMLElement) {
    syncIntroFocus(current);
  }
}

function watchIntroScroller() {
  introObserver?.disconnect();
  const intro = document.querySelector(".empty-state");
  if (!(intro instanceof HTMLElement)) {
    return;
  }
  introObserver ??= new ResizeObserver(syncCurrentIntro);
  syncIntroFocus(intro);
  intro.addEventListener("blur", () => syncIntroFocus(intro));
  // Observe the box and its content blocks: content can outgrow a box whose size
  // is unchanged (late font load, minimum font size).
  introObserver.observe(intro);
  for (const child of intro.children) {
    introObserver.observe(child);
  }
}

document.fonts?.ready.then(syncCurrentIntro);
watchIntroScroller();

document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-evidence-target]");
  if (!trigger) {
    return;
  }

  const dialog = document.getElementById(trigger.dataset.evidenceTarget);
  if (!(dialog instanceof HTMLDialogElement)) {
    return;
  }

  evidenceReturnTargets.set(dialog, trigger);
  if (!dialog.open) {
    dialog.showModal();
  }

  const heading = dialog.querySelector("[tabindex='-1']");
  if (heading instanceof HTMLElement) {
    heading.focus();
  }
});

document.addEventListener("close", (event) => {
  const dialog = event.target;
  if (!(dialog instanceof HTMLDialogElement)) {
    return;
  }

  const trigger = evidenceReturnTargets.get(dialog);
  if (trigger instanceof HTMLElement && document.contains(trigger)) {
    trigger.focus();
  }
}, true);

document.body.addEventListener("htmx:beforeRequest", (event) => {
  const source = event.target;
  if (!(source instanceof HTMLElement)) {
    return;
  }

  if (source.classList.contains("update-download-form")) {
    document.getElementById("knowledge-check-announcement").textContent = "Downloading and verifying the signed release.";
    source.setAttribute("aria-busy", "true");
    return;
  }

  if (source.classList.contains("update-check-form")) {
    document.getElementById("knowledge-check-announcement").textContent = "Checking for knowledge updates.";
    source.setAttribute("aria-busy", "true");
    return;
  }

  for (const [className, message] of requestStatusByFormClass) {
    if (source.classList.contains(className)) {
      announceStatus(message);
      return;
    }
  }
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  const target = event.detail?.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }

  if (target.id === "conversation-main") {
    const conversation = document.getElementById("conversation-main");
    if (conversation instanceof HTMLElement) {
      focusConversationTitle(conversation, { preventScroll: true });
      revealLatestTurn(conversation);
      watchIntroScroller();
    }
  }

  if (target.id === "knowledge-update-content") {
    const result = document.getElementById("manual-update-result");
    if (result) {
      document.getElementById("knowledge-check-announcement").textContent = result.textContent.trim();
      if (event.detail?.requestConfig?.elt?.classList.contains("update-download-form")) {
        document.getElementById("knowledge-update-title")?.focus();
      }
    }
  }

  const message = swapStatusByTargetId.get(target.id);
  if (message) {
    announceStatus(message);
  }
});

document.body.addEventListener("htmx:afterRequest", (event) => {
  const source = event.detail?.requestConfig?.elt ?? event.target;
  if (source instanceof HTMLElement && source.classList.contains("update-download-form")) {
    source.removeAttribute("aria-busy");
    if (event.detail?.failed || event.detail?.xhr?.status === 0) {
      let message = "Knowledge update download failed. Check the local app connection and retry.";
      try {
        const detail = JSON.parse(event.detail.xhr.responseText).detail;
        if (typeof detail === "string") message = detail;
      } catch {}
      document.getElementById("knowledge-check-announcement").textContent = message;
    }
    return;
  }

  if (event.target instanceof HTMLElement && event.target.classList.contains("update-check-form")) {
    event.target.removeAttribute("aria-busy");
    if (event.detail?.failed || event.detail?.xhr?.status === 0) {
      document.getElementById("knowledge-check-announcement").textContent =
        "Knowledge update check failed. Check the local app connection and retry.";
    }
  }
});

document.body.addEventListener("htmx:responseError", (event) => {
  if (event.target instanceof HTMLElement && (event.target.classList.contains("update-check-form") || event.target.classList.contains("update-download-form"))) return;
  announceStatus("Request failed. Review the visible error and retry.");
});
