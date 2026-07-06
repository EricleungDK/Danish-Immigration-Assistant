const evidenceReturnTargets = new WeakMap();

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
