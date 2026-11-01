(() => {
  const kind = document.querySelector("#plan-kind");
  const priority = document.querySelector("#plan-priority");
  if (!kind || !priority) return;
  const update = () => { priority.hidden = kind.value === "habit"; };
  kind.addEventListener("change", update);
  update();
})();
