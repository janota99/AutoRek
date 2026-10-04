// Takes an hour (0-23z) as an argument and returns a greeting
function getGreeting(hour) {
  var message;
  if (hour < 12) {
    message = "Good morning";
  } else if (hour < 18) {
    message = "Good afternoon";
  } else {
    message = "Good evening";
  }
  return message;
}

// Finds the current hour, then writes the greeting onto the page
function showGreeting() {
  var now = new Date();
  var hour = now.getHours();
  var text = getGreeting(hour);
  document.getElementById("greeting").innerHTML = text + "! Welcome to the Automated Outlook Invoice Tracker.";
}
showGreeting();
// Assumption used for the estimate (change it to whatever you can justify)
var MINUTES_SAVED_PER_INVOICE = 4;

// Takes two arguments and returns the hours saved
function estimateHoursSaved(invoices, minutesPerInvoice) {
  var totalMinutes = invoices * minutesPerInvoice;
  return totalMinutes / 60;
}

// Writes the result or an inline error into the result paragraph
function showSavingsMessage(html, isError) {
  var result = document.getElementById("savingsResult");
  result.innerHTML = html;
  result.classList.toggle("is-error", isError);
  result.setAttribute("role", isError ? "alert" : "status");
}

// Runs when the button is clicked
function showSavings() {
  var count = Number(document.getElementById("invoiceCount").value);

  if (isNaN(count) || count <= 0) {
    showSavingsMessage("Please enter a number of invoices greater than 0.", true);
    document.getElementById("invoiceCount").focus();
    return;
  }

  var hoursPerMonth = estimateHoursSaved(count, MINUTES_SAVED_PER_INVOICE);
  var hoursPerYear = hoursPerMonth * 12;

  showSavingsMessage(
    "Estimated time saved: <strong>" + hoursPerMonth.toFixed(1) +
    " hours per month</strong> (about " + hoursPerYear.toFixed(0) + " hours per year).", false);
}

// ---- Event handlers ----

// Typing in the invoice box updates the estimate as the person types.
// An empty or invalid value just clears the result (no alert while typing).
function updateSavingsLive() {
  var count = Number(document.getElementById("invoiceCount").value);
  var result = document.getElementById("savingsResult");

  if (isNaN(count) || count <= 0) {
    result.innerHTML = "";
    result.classList.remove("is-error");
    return;
  }
  showSavings();
}

// Mouse (and keyboard focus) on a feature card shows a worked example.
function showFeatureDetail(card) {
  if (card.querySelector(".feature-detail")) return;
  var detail = document.createElement("p");
  detail.className = "feature-detail";
  detail.textContent = card.getAttribute("data-detail");
  card.appendChild(detail);
  card.classList.add("is-expanded");
}

function hideFeatureDetail(card) {
  var detail = card.querySelector(".feature-detail");
  if (detail) detail.remove();
  card.classList.remove("is-expanded");
}

document.getElementById("calcButton").addEventListener("click", showSavings);
document.getElementById("invoiceCount").addEventListener("input", updateSavingsLive);

document.querySelectorAll(".feature-card[data-detail]").forEach(function (card) {
  card.tabIndex = 0;
  card.addEventListener("mouseenter", function () { showFeatureDetail(card); });
  card.addEventListener("mouseleave", function () { hideFeatureDetail(card); });
  card.addEventListener("focus", function () { showFeatureDetail(card); });
  card.addEventListener("blur", function () { hideFeatureDetail(card); });
});
