(() => {
  const input = document.querySelector("#plan-file");
  const name = document.querySelector("#plan-file-name");
  if (!input || !name) return;
  input.addEventListener("change", () => {
    name.textContent = input.files?.[0]?.name || "Chưa chọn tệp";
  });
})();
