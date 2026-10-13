/* ИТС — интерактив: cookie-согласие (152-ФЗ/149-ФЗ) + лёгкие анимации */
(function () {
  "use strict";

  /* ---------- Cookie-баннер ---------- */
  var KEY = "itres_cookie_consent_v1"; // значение: "all" | "necessary" | JSON настроек
  function getConsent() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function setConsent(v) {
    try { localStorage.setItem(KEY, v); } catch (e) {}
  }

  var banner = document.getElementById("cookie-banner");
  if (banner && !getConsent()) {
    banner.classList.add("show");
    banner.removeAttribute("hidden");

    var save = function (value) {
      setConsent(value);
      banner.classList.remove("show");
      document.dispatchEvent(new CustomEvent("consent:" + value));
    };
    var byId = function (id) { return document.getElementById(id); };

    byId("cookie-accept") && byId("cookie-accept").addEventListener("click", function () { save("all"); });
    byId("cookie-reject") && byId("cookie-reject").addEventListener("click", function () { save("necessary"); });

    // «Настроить»: сохраняем выбор по категориям
    var settingsBtn = byId("cookie-settings");
    var details = byId("cookie-details");
    settingsBtn && settingsBtn.addEventListener("click", function () {
      details.hidden = !details.hidden;
      settingsBtn.textContent = details.hidden ? "Настроить" : "Сохранить выбор";
      if (details.hidden) {
        var analytics = byId("cookie-analytics"),
            marketing = byId("cookie-marketing");
        save(JSON.stringify({
          necessary: true,
          analytics: analytics ? analytics.checked : false,
          marketing: marketing ? marketing.checked : false
        }));
      }
    });

    // ссылка «Изменить согласие» в подвале
    Array.prototype.forEach.call(document.querySelectorAll("[data-open-cookie-settings]"), function (el) {
      el.addEventListener("click", function (ev) {
        ev.preventDefault();
        setConsent("");           // сбрасываем, чтобы баннер снова появился
        banner.classList.add("show");
        if (details) details.hidden = false;
      });
    });
  }

  /* ---------- Ленивая загрузка картинок (нативная fallback) ---------- */
  document.querySelectorAll("img[data-src]").forEach(function (img) {
    if ("loading" in img) { img.loading = "lazy"; }
    img.src = img.getAttribute("data-src");
  });

  /* ---------- Появление секций при скролле ---------- */
  if ("IntersectionObserver" in window &&
      !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("visible"); io.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    document.querySelectorAll(".reveal").forEach(function (el) { io.observe(el); });
  } else {
    document.querySelectorAll(".reveal").forEach(function (el) { el.classList.add("visible"); });
  }
})();
