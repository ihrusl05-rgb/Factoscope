(function () {
  "use strict";

  function attach(el) {
    var wrap = document.createElement("div");
    wrap.className = "charcount-help";
    wrap.style.cssText = "color:#666; font-size:11px; margin-top:4px;";
    el.parentNode.insertBefore(wrap, el.nextSibling);

    var max = parseInt(el.getAttribute("data-charcount-max"), 10);

    function count() {
      var n = el.value.length;
      var breaks = (el.value.match(/\n/g) || []).length;
      var text;
      if (max > 0) {
        text = n + " / " + max + " символов";
        wrap.style.color = n > max ? "#ba2121" : "#666";
      } else {
        text = n + " символов (" + breaks + " переносов)";
      }
      wrap.textContent = text;
    }

    el.addEventListener("input", count);
    count();
  }

  function init() {
    var boxes = document.querySelectorAll("textarea[data-charcount]");
    for (var i = 0; i < boxes.length; i++) {
      attach(boxes[i]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();