/*
  Выдвижное боковое меню на узкой ширине.

  На широком экране меню — обычная колонка грида, и скрипт ей не нужен:
  ни один обработчик ниже ничего не делает, пока кнопка скрыта стилями.
  Поэтому состояние живёт в классе на самом меню, а не в JS: без
  скрипта (ошибка загрузки, отключённый JS) на широком экране всё
  работает, а на узком меню просто остаётся закрытым — навигация при
  этом не теряется, все её пункты доступны с любой страницы.
*/
(function () {
  "use strict";

  var sidebar = document.getElementById("sidebar");
  var toggle = document.getElementById("sidebar-toggle");
  if (!sidebar || !toggle) {
    return;
  }

  var scrim = null;

  function close() {
    sidebar.classList.remove("is-open");
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Показать меню");
    if (scrim) {
      scrim.remove();
      scrim = null;
    }
  }

  function open() {
    sidebar.classList.add("is-open");
    toggle.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-label", "Скрыть меню");
    // Подложка — <button>, а не <div>: по ней кликают, чтобы закрыть
    // меню, а значит она обязана быть достижимой с клавиатуры и иметь
    // название. <div onclick> дал бы ловушку фокуса для тех, кто
    // клавиатурой и пользуется.
    scrim = document.createElement("button");
    scrim.type = "button";
    scrim.className = "sidebar-scrim";
    scrim.setAttribute("aria-label", "Закрыть меню");
    scrim.addEventListener("click", close);
    document.body.appendChild(scrim);
  }

  toggle.addEventListener("click", function () {
    if (sidebar.classList.contains("is-open")) {
      close();
    } else {
      open();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && sidebar.classList.contains("is-open")) {
      close();
      toggle.focus();
    }
  });

  // Переход по пункту меню закрывает его сам: на узкой ширине меню
  // перекрывает страницу, и оставлять его открытым поверх только что
  // открытого раздела незачем.
  sidebar.addEventListener("click", function (event) {
    if (event.target.closest("a") && sidebar.classList.contains("is-open")) {
      close();
    }
  });
})();
