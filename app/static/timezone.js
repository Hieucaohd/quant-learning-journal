// Report the browser's time zone to the server (read in app/timezone.py) so
// "today" on the schedule is the user's local day, not the server's UTC day.
(() => {
  let zone;
  try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return;
  }
  if (!zone) return;

  const current = document.cookie.split("; ")
    .find((item) => item.startsWith("tz="))?.slice(3);
  if (current && decodeURIComponent(current) === zone) return;

  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `tz=${encodeURIComponent(zone)}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;

  // Re-render once with the right zone. replace() navigates with GET, so a
  // page produced by a form POST is never resubmitted; the flag stops a loop
  // when cookies are blocked.
  const flag = `tz-reloaded:${zone}`;
  try {
    if (sessionStorage.getItem(flag)) return;
    sessionStorage.setItem(flag, "1");
  } catch {
    return;
  }
  location.replace(location.href);
})();
