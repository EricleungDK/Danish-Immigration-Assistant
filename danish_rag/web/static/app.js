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

// The top bar and snapshot banner wrap at narrow widths; size the layout from their
// real height instead of a fixed constant.
function syncChromeHeight() {
  let height = 0;
  for (const selector of [".topbar", ".snapshot-banner"]) {
    const element = document.querySelector(selector);
    if (element instanceof HTMLElement) {
      height += element.getBoundingClientRect().height;
    }
  }
  document.documentElement.style.setProperty("--chrome-height", `${height}px`);
}

syncChromeHeight();
if ("ResizeObserver" in window) {
  const chromeObserver = new ResizeObserver(syncChromeHeight);
  for (const selector of [".topbar", ".snapshot-banner"]) {
    const element = document.querySelector(selector);
    if (element) {
      chromeObserver.observe(element);
    }
  }
} else {
  window.addEventListener("resize", syncChromeHeight);
}

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
