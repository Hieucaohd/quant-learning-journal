(() => {
  const pageSelector = "#schedule-page";
  let controller;

  async function loadSchedule(url, pushHistory = true) {
    const page = document.querySelector(pageSelector);
    if (!page) return;

    controller?.abort();
    controller = new AbortController();
    page.setAttribute("aria-busy", "true");
    page.classList.add("is-loading");

    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`Máy chủ trả về mã ${response.status}`);

      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      const updated = doc.querySelector(pageSelector);
      if (!updated) throw new Error("Không đọc được lịch vừa cập nhật");

      page.replaceWith(updated);
      document.title = doc.title;
      if (pushHistory) history.pushState({ schedule: true }, "", response.url);
    } catch (error) {
      if (error.name === "AbortError") return;
      window.location.assign(url);
    }
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("#schedule-page .calendar-day");
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey ||
        event.shiftKey || event.altKey) return;
    event.preventDefault();
    loadSchedule(link.href);
  });

  window.addEventListener("popstate", () => {
    if (document.querySelector(pageSelector)) loadSchedule(window.location.href, false);
  });
})();
