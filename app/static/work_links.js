(() => {
  function openWork() {
    const key = window.location.hash.slice(1);
    if (!/^(task|lecture)-\d+$/.test(key)) return;
    const target = document.getElementById(key);
    if (!target || !target.matches("details")) return;
    let detail = target;
    while (detail) {
      detail.open = true;
      detail = detail.parentElement?.closest("details");
    }
    requestAnimationFrame(() => {
      target.querySelector("summary")?.focus({ preventScroll: true });
      target.scrollIntoView({ block: "start", behavior: "auto" });
    });
  }
  window.addEventListener("hashchange", openWork);
  openWork();
})();
