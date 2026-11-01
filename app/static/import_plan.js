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

(() => {
  const mode = document.querySelector("#import-type");
  const plan = document.querySelector("#target-plan-field");
  const habitPlan = document.querySelector("#habit-plan-field");
  const habitKit = document.querySelector("#habit-kit-download");
  const habitSelect = habitPlan?.querySelector("select");
  if (!mode || !plan) return;
  const update = () => {
    plan.hidden = mode.value !== "overwrite_course";
    const select = plan.querySelector("select");
    if (select) select.required = mode.value === "overwrite_course";
    if (habitPlan) habitPlan.hidden = mode.value !== "habit";
  };
  mode.addEventListener("change", update);
  update();
  habitSelect?.addEventListener("change", () => {
    if (!habitKit) return;
    const url = new URL(habitKit.href);
    if (habitSelect.value) url.searchParams.set("plan_id", habitSelect.value);
    else url.searchParams.delete("plan_id");
    habitKit.href = url.href;
  });
  document.querySelector("#import-preview")?.scrollIntoView({block: "start"});
})();
