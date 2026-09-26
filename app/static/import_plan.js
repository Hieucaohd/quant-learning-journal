(() => {
  const input = document.querySelector("#plan-file");
  const name = document.querySelector("#plan-file-name");
  if (!input || !name) return;
  input.addEventListener("change", () => {
    name.textContent = input.files?.[0]?.name || "Chưa chọn tệp";
  });
})();

(() => {
  const button = document.querySelector("#copy-ai-prompt");
  const prompt = document.querySelector("#ai-prompt");
  const status = document.querySelector("#copy-ai-prompt-status");
  if (!button || !prompt || !status) return;
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(prompt.value);
    } catch {
      prompt.select();
      if (!document.execCommand("copy")) {
        status.textContent = "Không sao chép được, hãy chọn và sao chép thủ công.";
        return;
      }
    }
    status.textContent = "Đã sao chép prompt.";
  });
})();
