(function () {
  var button = document.querySelector(".copy");
  if (!button) {
    return;
  }
  function flash(message) {
    button.textContent = message;
    window.setTimeout(function () {
      button.textContent = "Copy link";
    }, 2500);
  }
  function fallback(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    document.body.appendChild(area);
    area.select();
    var copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (error) {
      copied = false;
    }
    document.body.removeChild(area);
    if (copied) {
      flash("Copied");
    } else {
      button.textContent = "Copy did not work. Select the link above.";
    }
  }
  button.addEventListener("click", function () {
    var text = button.getAttribute("data-link");
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () {
        flash("Copied");
      }, function () {
        fallback(text);
      });
      return;
    }
    fallback(text);
  });
})();
