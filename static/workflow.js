// Live Workflow Monitor: asks the server for the latest status and updates the table WITHOUT reloading.
//
// Cost and safety limits:
//  - The SERVER decides whether to ask again ("poll") and how soon ("next_poll_ms"): every 2 seconds at
//    first, then every 5 seconds, and never after the workflow finished or WORKFLOW_POLL_LIMIT_SECONDS.
//  - While the browser tab is hidden, no requests are sent.
//  - After 3 failed requests in a row, updates stop until the page is reloaded.
//  - Server text is inserted with textContent, never as HTML, so it can never run as a script.
(function () {
  var monitor = document.getElementById("workflow-monitor");
  if (!monitor) {
    return; // Not a workflow page.
  }
  var statusUrl = monitor.getAttribute("data-status-url");
  var pollStatus = document.getElementById("poll-status");
  var failures = 0;

  // Builds a badge like the server's status_badge macro, for example <span class="badge badge-timed-out">.
  function makeBadge(state) {
    var badge = document.createElement("span");
    badge.className = "badge badge-" + state.toLowerCase().replace(/ /g, "-");
    badge.textContent = state;
    return badge;
  }

  function makeTag(text, className) {
    var tag = document.createElement("span");
    tag.className = className;
    tag.textContent = text;
    return tag;
  }

  // Puts the new data into the existing table rows.
  function render(view) {
    var overall = document.getElementById("overall");
    overall.replaceChildren(makeBadge(view.overall));

    view.stages.forEach(function (stage) {
      var row = document.querySelector('tr[data-stage="' + stage.id + '"]');
      if (!row) {
        return;
      }
      var stateCell = row.querySelector(".stage-state");
      stateCell.replaceChildren(makeBadge(stage.state));
      if (stage.simulated) {
        stateCell.append(" ", makeTag("SIMULATED", "tag-simulated"));
      }
      if (stage.attempt > 1) {
        stateCell.append(" ", makeTag("attempt " + stage.attempt, "small"));
      }
      row.querySelector(".stage-time").textContent =
        stage.duration_seconds === null ? "-" : stage.duration_seconds.toFixed(2) + " s";
      row.querySelector(".stage-detail").textContent = stage.detail || "";
    });

    var checked = view.checked_at.substring(11, 19); // The time part, for example "10:42:07".
    if (view.poll) {
      pollStatus.textContent = "Updating automatically · last checked " + checked + " UTC";
    } else if (["Running", "Retrying", "Pending"].indexOf(view.overall) >= 0) {
      pollStatus.textContent = "Automatic updates stopped (time limit). Reload the page to check again.";
    } else {
      pollStatus.textContent = "Finished · last checked " + checked + " UTC. No more updates needed.";
    }
  }

  function scheduleCheck(milliseconds) {
    window.setTimeout(checkNow, milliseconds);
  }

  function checkNow() {
    if (document.hidden) {
      scheduleCheck(5000); // The tab is not visible: wait without sending a request.
      return;
    }
    fetch(statusUrl, { headers: { "Accept": "application/json" }, cache: "no-store", credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (view) {
        failures = 0;
        render(view);
        if (view.poll) {
          scheduleCheck(view.next_poll_ms);
        }
      })
      .catch(function () {
        failures += 1;
        if (failures < 3) {
          pollStatus.textContent = "Connection problem, trying again…";
          scheduleCheck(5000);
        } else {
          pollStatus.textContent = "Updates stopped after 3 failed requests. Reload the page to try again.";
        }
      });
  }

  if (monitor.getAttribute("data-poll") === "true") {
    scheduleCheck(parseInt(monitor.getAttribute("data-next"), 10) || 2000);
  }
})();
