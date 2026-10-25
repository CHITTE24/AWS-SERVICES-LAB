// Small browser script: while an AWS request is running, disable its "Run" button.
// This stops accidental double-clicks from sending the same request twice.

// Finds every form with the class "run-form" (the operation and experiment forms).
document.querySelectorAll("form.run-form").forEach(function (form) {
  // Runs when the form is submitted.
  form.addEventListener("submit", function () {
    var button = form.querySelector("button[type=submit]");
    if (button) {
      button.disabled = true;           // Greys out the button so it cannot be clicked again.
      button.textContent = "Running…";  // Shows that something is happening.
    }
  });
});
