(function () {
  var forms = document.querySelectorAll("form");
  Array.prototype.forEach.call(forms, function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector('button[type="submit"]');
      if (!button || button.disabled) {
        return;
      }
      button.textContent = "Saving";
      window.setTimeout(function () {
        button.disabled = true;
      }, 0);
    });
  });
})();
