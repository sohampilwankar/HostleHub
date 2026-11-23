// HostelHub - small progressive enhancements (the app works without JS).
document.addEventListener("DOMContentLoaded", function () {
  // Confirmation prompt for destructive forms: <form data-confirm="...">
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  // Bootstrap client-side validation for forms with .needs-validation
  document.querySelectorAll("form.needs-validation").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!form.checkValidity()) {
        e.preventDefault();
        e.stopPropagation();
      }
      form.classList.add("was-validated");
    });
  });

  // Login page: switch the identifier label between Email and Username,
  // and only offer the signup link for students.
  var label = document.getElementById("identifier-label");
  var signupPrompt = document.getElementById("signup-prompt");
  if (label) {
    document.querySelectorAll("input[name=role]").forEach(function (radio) {
      radio.addEventListener("change", function () {
        label.textContent = radio.value === "admin" ? "Username" : "Email address";
        if (signupPrompt) signupPrompt.hidden = radio.value === "admin";
      });
    });
  }

  // Confirm-password fields: <input data-match="#password">
  document.querySelectorAll("input[data-match]").forEach(function (confirmInput) {
    var original = document.querySelector(confirmInput.dataset.match);
    if (!original) return;
    function check() {
      confirmInput.setCustomValidity(
        confirmInput.value && confirmInput.value !== original.value ? "Passwords do not match" : "");
    }
    confirmInput.addEventListener("input", check);
    original.addEventListener("input", check);
  });

  // Room form: suggest a capacity from the room type when capacity is empty
  var typeSelect = document.querySelector("select[data-capacity-target]");
  if (typeSelect) {
    var capacity = document.querySelector(typeSelect.dataset.capacityTarget);
    var defaults = { Single: 1, Double: 2, Triple: 3 };
    typeSelect.addEventListener("change", function () {
      var suggested = defaults[typeSelect.value];
      if (suggested && capacity && !capacity.value) {
        capacity.value = Math.min(suggested, parseInt(capacity.dataset.max, 10) || suggested);
      }
    });
  }

  // Auto-dismiss success messages after a few seconds
  document.querySelectorAll(".alert-success.alert-dismissible").forEach(function (el) {
    setTimeout(function () {
      if (window.bootstrap) bootstrap.Alert.getOrCreateInstance(el).close();
    }, 5000);
  });
});
