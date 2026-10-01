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

// Runs when the button is clicked
function showSavings() {
  var count = Number(document.getElementById("invoiceCount").value);

  if (isNaN(count) || count <= 0) {
    alert("Please enter a number of invoices greater than 0.");
    return;
  }

  var hoursPerMonth = estimateHoursSaved(count, MINUTES_SAVED_PER_INVOICE);
  var hoursPerYear = hoursPerMonth * 12;

  document.getElementById("savingsResult").innerHTML =
    "Estimated time saved: <strong>" + hoursPerMonth.toFixed(1) +
    " hours per month</strong> (about " + hoursPerYear.toFixed(0) + " hours per year).";
}