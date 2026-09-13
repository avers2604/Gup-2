/*
  Переключатель темы.

  Токены [data-theme="dark"] были объявлены с самого веб-порта, но атрибут
  никто не ставил: работала только системная настройка
  prefers-color-scheme, а половина написанного кода темы была
  недостижима. Диспетчерская и ночная смена — ровно тот случай, когда
  выбор нужен независимо от настройки ОС.

  Три состояния, а не два. «Как в системе» — не то же самое, что «светлая»:
  пользователь, переключивший ОС на ночной режим, ждёт, что приложение
  последует за ней. Сбросить выбор обратно к системному иначе нечем.

  Применение темы делает НЕ этот файл, а маленький встроенный скрипт в
  <head> (templates/base.html): он выполняется до первой отрисовки. Здесь
  бы это случилось после — и страница мигнула бы светлым, прежде чем
  стать тёмной.
*/
(function () {
  "use strict";

  var STORAGE_KEY = "bz-get-theme";
  var ORDER = ["system", "light", "dark"];
  var LABELS = {
    system: "Тема: как в системе",
    light: "Тема: светлая",
    dark: "Тема: тёмная"
  };
  var ICONS = { system: "◐", light: "☀", dark: "☾" };

  function stored() {
    try {
      var value = window.localStorage.getItem(STORAGE_KEY);
      return ORDER.indexOf(value) === -1 ? "system" : value;
    } catch (error) {
      // Приватный режим и запрет хранилища: тема просто не запоминается,
      // но переключатель обязан работать в пределах страницы.
      return "system";
    }
  }

  function apply(theme) {
    var root = document.documentElement;
    if (theme === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", theme);
    }
  }

  function render(button, theme) {
    button.textContent = ICONS[theme];
    button.setAttribute("aria-label", LABELS[theme]);
    button.setAttribute("title", LABELS[theme]);
  }

  function init() {
    var button = document.getElementById("theme-toggle");
    if (!button) {
      return;
    }

    var current = stored();
    render(button, current);

    button.addEventListener("click", function () {
      current = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
      apply(current);
      render(button, current);
      try {
        window.localStorage.setItem(STORAGE_KEY, current);
      } catch (error) {
        /* см. stored(): без хранилища выбор живёт до перезагрузки */
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
