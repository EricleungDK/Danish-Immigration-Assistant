const evidenceReturnTargets = new WeakMap();
const requestStatusByFormClass = new Map([
  ["composer", "Preparing answer."],
  ["setup-form", "Testing provider."],
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

function focusConversationTitle(root = document) {
  const title = root.querySelector("#conversation-title");
  if (title instanceof HTMLElement) {
    title.focus();
  }
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
    focusConversationTitle(document);
  }

  const message = swapStatusByTargetId.get(target.id);
  if (message) {
    announceStatus(message);
  }
});

document.body.addEventListener("htmx:responseError", () => {
  announceStatus("Request failed. Review the visible error and retry.");
});
