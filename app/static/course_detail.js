(() => {
  const main = document.querySelector("main.shell");
  const page = main?.querySelector("#course-detail");
  if (!page) return;

  const coursePath = new URL(page.dataset.courseUrl, window.location.href).pathname;
  let saving = false;
  let statusTimer;

  function showStatus(message, error = false) {
    const status = main.querySelector("#save-status");
    if (!status) return;
    clearTimeout(statusTimer);
    status.textContent = message;
    status.classList.toggle("error", error);
    status.hidden = false;
    if (!error) statusTimer = setTimeout(() => { status.hidden = true; }, 4500);
  }

  main.addEventListener("change", (event) => {
    const input = event.target;
    if (input instanceof HTMLInputElement && input.matches("#course-detail [data-autosubmit]")) {
      input.form?.requestSubmit();
    }
  });

  main.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.closest("#course-detail")) return;
    if (event.defaultPrevented) return;
    event.preventDefault();
    if (saving) return;

    const opened = new Set(Array.from(main.querySelectorAll("details[data-open-key][open]"),
                                       (item) => item.dataset.openKey));
    const anchor = form.closest("details[data-open-key]");
    const anchorKey = anchor?.dataset.openKey;
    const anchorTop = anchor?.getBoundingClientRect().top;
    const oldScroll = window.scrollY;
    const body = event.submitter ? new FormData(form, event.submitter) : new FormData(form);
    const buttons = Array.from(form.querySelectorAll('button[type="submit"]'));

    saving = true;
    form.setAttribute("aria-busy", "true");
    buttons.forEach((button) => { button.disabled = true; });
    showStatus("Đang lưu...");
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!response.ok) throw new Error(`Máy chủ trả về mã ${response.status}`);
      if (new URL(response.url).pathname !== coursePath) {
        window.location.assign(response.url);
        return;
      }
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      const updatedMain = doc.querySelector("main.shell");
      if (!updatedMain?.querySelector("#course-detail")) throw new Error("Không đọc được trang vừa cập nhật");

      const notices = Array.from(updatedMain.querySelectorAll(".notice[role='status']"));
      main.innerHTML = updatedMain.innerHTML;
      document.title = doc.title;
      main.querySelectorAll("details[data-open-key]").forEach((item) => {
        item.open = opened.has(item.dataset.openKey);
      });
      showStatus(notices.map((item) => item.textContent.trim()).join(" ") || "Đã cập nhật.",
                 notices.some((item) => item.classList.contains("error")));

      requestAnimationFrame(() => {
        const newAnchor = anchorKey && Array.from(main.querySelectorAll("details[data-open-key]"))
          .find((item) => item.dataset.openKey === anchorKey);
        const target = newAnchor && anchorTop !== undefined
          ? newAnchor.getBoundingClientRect().top + window.scrollY - anchorTop
          : oldScroll;
        window.scrollTo({ top: target, behavior: "auto" });
      });
    } catch (error) {
      form.removeAttribute("aria-busy");
      buttons.forEach((button) => { button.disabled = false; });
      showStatus(`Không thể cập nhật tại chỗ: ${error.message}. Hãy tải lại trang để kiểm tra dữ liệu.`, true);
    } finally {
      saving = false;
    }
  });
})();
