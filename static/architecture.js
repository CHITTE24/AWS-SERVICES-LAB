// Architecture Map: draw your own architecture and see what recorded evidence says about each part.
//
// The SERVER decides every colour, label, and explanation (experiments/architecture_evidence.py).
// This script only draws them and sends your edits. Rules it follows:
//  - All server text is inserted with textContent, never as HTML.
//  - A box or arrow that is new or changed is grey until the diagram is saved and its evidence is loaded.
//  - The automatic evidence refresh never runs while there are unsaved changes or while you are typing in
//    the side panel, so it can never overwrite your edits or show old evidence on a changed box.
//  - Nothing here can create, connect, or change anything in AWS. "Verify Now" asks the server for ONE
//    read-only check of ONE box.
(function () {
  "use strict";
  var page = document.getElementById("arch-page");
  if (!page || typeof cytoscape === "undefined") {
    return;
  }
  var design = page.getAttribute("data-mode") === "design";
  var COLOURS = { green: "#2f855a", red: "#c53030", yellow: "#b7791f", gray: "#8a94a3" };
  var SYMBOLS = { green: "✓", red: "✗", yellow: "!", gray: "–" };
  var state = { catalog: null, diagramId: null, version: null, name: "", dirty: false, evidence: null,
                scope: { account: "", region: "" }, connectSource: null, connecting: false, openedAt: Date.now() };
  var statusBox = document.getElementById("arch-status");
  var details = document.getElementById("details");

  if (window.cytoscapeDagre) {
    try { cytoscape.use(window.cytoscapeDagre); } catch (alreadyRegistered) { /* registered earlier */ }
  }

  // ---------------------------------------------------------------- small helpers

  function el(tag, text, className) {
    var element = document.createElement(tag);
    if (text !== undefined && text !== null) { element.textContent = text; }
    if (className) { element.className = className; }
    return element;
  }

  function say(message, isError) {
    statusBox.textContent = message;
    statusBox.className = "small" + (isError ? " arch-status-error" : "");
  }

  function randomId(prefix) {
    return prefix + Math.random().toString(36).slice(2, 10);
  }

  function api(method, url, body) {
    var options = { method: method, credentials: "same-origin", cache: "no-store",
                    headers: { "Accept": "application/json" } };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(url, options).then(function (response) {
      var type = response.headers.get("content-type") || "";
      if (type.indexOf("application/json") < 0) {
        throw new Error("Your login has expired. Reload the page and log in again.");
      }
      return response.json().then(function (data) {
        if (!response.ok) { throw new Error(data.error || ("Request failed (" + response.status + ").")); }
        return data;
      });
    });
  }

  function markDirty() {
    state.dirty = true;
    say("Unsaved changes. Save to check them against the evidence.");
  }

  // ---------------------------------------------------------------- the canvas

  var cy = cytoscape({
    container: document.getElementById("cy"),
    wheelSensitivity: 0.3,
    minZoom: 0.1,
    maxZoom: 3,
    autoungrabify: !design,
    style: [
      { selector: "node", style: {
          "shape": "round-rectangle", "width": 160, "height": 70, "background-color": "#ffffff",
          "border-width": 3, "border-color": "data(colourHex)", "label": "data(display)", "text-wrap": "wrap",
          "text-max-width": 150, "font-size": design ? 10 : 13, "text-valign": "center", "color": "#1f2933" } },
      { selector: "node[icon]", style: {
          "background-image": "data(icon)", "background-fit": "none", "background-width": 26, "background-height": 26,
          "background-position-y": "4px", "text-margin-y": 12, "height": 84 } },
      { selector: "node[basis = 'manual']", style: { "border-style": "dotted", "border-width": 5 } },
      { selector: ":parent", style: {
          "background-opacity": 0.05, "background-color": "#0b5cad", "border-style": "dashed",
          "text-valign": "top", "text-halign": "center", "padding": 28 } },
      { selector: "edge", style: {
          "width": 3, "curve-style": "bezier", "target-arrow-shape": "triangle", "line-color": "data(colourHex)",
          "target-arrow-color": "data(colourHex)", "label": "data(display)", "font-size": design ? 9 : 12,
          "text-wrap": "wrap", "text-background-color": "#ffffff", "text-background-opacity": 0.9,
          "text-background-padding": 2, "color": "#1f2933" } },
      { selector: "edge[basis = 'manual']", style: { "line-style": "dotted", "width": 4 } },
      { selector: "edge[basis = 'none'], edge[basis = 'simulated'], edge[basis = 'unsaved']",
        style: { "line-style": "dashed" } },
      { selector: ":selected", style: { "overlay-color": "#0b5cad", "overlay-opacity": 0.15 } },
      { selector: ".connect-source", style: { "border-color": "#0b5cad", "border-width": 6 } }
    ]
  });

  function typeInfo(type) { return state.catalog.types[type] || { label: type }; }

  function statusText(result) {
    if (!result) { return "– Not verified (save to check)"; }
    var text = SYMBOLS[result.colour] + " " + result.label;
    if (result.basis === "manual") { text = "MANUAL · " + text; }
    if (result.basis === "simulated") { text = "SIMULATED"; }
    return text;
  }

  function resultFor(element) {
    if (!state.evidence || element.data("unsaved")) { return null; }
    var group = element.isNode() ? state.evidence.nodes : state.evidence.edges;
    return group[element.id()] || null;
  }

  // Writes the colour and the visible text of one element from its evidence.
  function paint(element) {
    var result = resultFor(element);
    var colour = result ? result.colour : "gray";
    element.data("colourHex", COLOURS[colour]);
    element.data("basis", result ? result.basis : "unsaved");
    if (element.isNode()) {
      var name = element.data("label") || element.data("identifier") || "(no identifier yet)";
      var lines = [typeInfo(element.data("type")).label, name];
      if (element.data("label") && element.data("identifier")) { lines.push(element.data("identifier")); }
      lines.push(statusText(result));
      element.data("display", lines.join("\n"));
    } else {
      element.data("display", (element.data("label") || "") + "\n" + statusText(result));
    }
  }

  function paintAll() { cy.elements().forEach(paint); }

  function bodyFromCanvas() {
    return {
      nodes: cy.nodes().map(function (node) {
        return { id: node.id(), type: node.data("type"), identifier: node.data("identifier") || "",
                 label: node.data("label") || "", parent: node.data("parent") || null,
                 x: Math.round(node.position("x")), y: Math.round(node.position("y")) };
      }),
      edges: cy.edges().map(function (edge) {
        return { id: edge.id(), source: edge.data("source"), target: edge.data("target"),
                 operation: edge.data("operation"), label: edge.data("label") || "" };
      }),
      scope: { account: state.scope.account || "", region: state.scope.region || "" }
    };
  }

  function canvasFromBody(body) {
    cy.elements().remove();
    state.scope = body.scope || { account: "", region: "" };
    var elements = [];
    // Containers (VPC / subnet) must exist before the boxes inside them: add boxes in order of nesting depth.
    var byId = {};
    body.nodes.forEach(function (node) { byId[node.id] = node; });
    function depth(node) {
      var level = 0, current = node;
      while (current.parent && byId[current.parent] && level < body.nodes.length) { current = byId[current.parent]; level++; }
      return level;
    }
    body.nodes.slice().sort(function (a, b) { return depth(a) - depth(b); }).forEach(function (node) {
      var data = { id: node.id, type: node.type, identifier: node.identifier, label: node.label };
      if (node.parent) { data.parent = node.parent; }
      var icon = typeInfo(node.type).icon;
      if (icon) { data.icon = icon; }
      elements.push({ group: "nodes", data: data, position: { x: node.x, y: node.y } });
    });
    body.edges.forEach(function (edge) {
      elements.push({ group: "edges", data: { id: edge.id, source: edge.source, target: edge.target,
                                              operation: edge.operation, label: edge.label } });
    });
    cy.add(elements);
    paintAll();
    var unplaced = body.nodes.length > 1 && body.nodes.every(function (node) { return node.x === 0 && node.y === 0; });
    if (unplaced) { runLayout(); } else { cy.fit(undefined, 40); }
  }

  function runLayout() {
    cy.layout({ name: window.cytoscapeDagre ? "dagre" : "breadthfirst", rankDir: "LR", nodeSep: 40, rankSep: 110,
                fit: true, padding: 40 }).run();
    if (design && state.diagramId) { markDirty(); }
  }

  // ---------------------------------------------------------------- loading and saving

  function typingInPanel() {
    return details.contains(document.activeElement) &&
      ["INPUT", "TEXTAREA", "SELECT"].indexOf(document.activeElement.tagName) >= 0;
  }

  // automatic = the 30-second refresh. It is skipped while there are unsaved changes or you are typing.
  function loadEvidence(automatic) {
    if (!state.diagramId) { return Promise.resolve(); }
    if (automatic && (state.dirty || typingInPanel())) { return Promise.resolve(); }
    return api("GET", "/api/architecture/diagrams/" + state.diagramId + "/evidence").then(function (evidence) {
      state.evidence = evidence;
      // Only a saved diagram matches its evidence; unsaved boxes and arrows stay grey ("save to check").
      if (!state.dirty) { cy.elements().forEach(function (element) { element.removeData("unsaved"); }); }
      paintAll();
      document.getElementById("legend-window").textContent =
        "Freshness window: " + evidence.max_age_hours + " h · evidence read " + evidence.generated_at.slice(11, 19) +
        " UTC · " + evidence.unattributed.total + " unattributed record(s) (never shown on a box)";
      if (!typingInPanel()) {
        var selected = cy.$(":selected");
        if (selected.length === 1) { showDetails(selected[0]); } else { showDiagramSettings(); }
      }
    }).catch(function (error) { say(error.message, true); });
  }

  function loadDiagramList(selectId) {
    return api("GET", "/api/architecture/diagrams").then(function (data) {
      var select = document.getElementById("diagram-select");
      select.replaceChildren();
      if (data.diagrams.length === 0) {
        select.append(el("option", design ? "No diagrams yet: create one" : "No diagrams yet (use Design mode)"));
        details.replaceChildren(el("p", design ? "Create a blank diagram, or start from this app's settings."
                                                : "Open Design mode to create a diagram.", "muted"));
        return;
      }
      data.diagrams.forEach(function (diagram) {
        var option = el("option", diagram.name + " (v" + diagram.version + ")");
        option.value = diagram.id;
        select.append(option);
      });
      var chosen = selectId || data.diagrams[0].id;
      select.value = String(chosen);
      return loadDiagram(chosen);
    }).catch(function (error) { say(error.message, true); });
  }

  function loadDiagram(diagramId) {
    return api("GET", "/api/architecture/diagrams/" + diagramId).then(function (diagram) {
      state.diagramId = diagram.id;
      state.version = diagram.version;
      state.name = diagram.name;
      state.dirty = false;
      state.evidence = null;
      canvasFromBody(diagram.body);
      say("Loaded \"" + diagram.name + "\" (version " + diagram.version + ").");
      return loadEvidence();
    }).catch(function (error) { say(error.message, true); });
  }

  function saveDiagram() {
    if (!state.diagramId) { say("Create a diagram first.", true); return; }
    api("PUT", "/api/architecture/diagrams/" + state.diagramId,
        { name: state.name, version: state.version, body: bodyFromCanvas() }).then(function (result) {
      state.version = result.version;
      state.dirty = false;
      say("Saved (version " + result.version + ").");
      return loadDiagramList(state.diagramId);
    }).catch(function (error) { say(error.message, true); });
  }

  function createDiagram(template) {
    var name = window.prompt("Name of the new diagram:", template === "starter" ? "AWS Services Lab" : "My architecture");
    if (!name) { return; }
    api("POST", "/api/architecture/diagrams", { name: name, template: template }).then(function (result) {
      return loadDiagramList(result.id);
    }).catch(function (error) { say(error.message, true); });
  }

  // ---------------------------------------------------------------- the details panel

  function kv(label, value) {
    var row = el("div", null, "kv");
    row.append(el("b", label + ": "), document.createTextNode(value || "-"));
    return row;
  }

  function field(labelText, input) {
    var label = el("label", labelText);
    label.append(input);
    return label;
  }

  function textInput(value, maxLength, onChange) {
    var input = document.createElement("input");
    input.type = "text";
    input.value = value || "";
    input.maxLength = maxLength;
    input.addEventListener("input", function () { onChange(input.value); });
    return input;
  }

  function evidenceSection(result) {
    var section = el("div", null, "section");
    section.append(el("h3", "Verification"));
    if (!result) {
      section.append(el("p", "Not verified yet: save the diagram to check this element against the evidence.", "small"));
      return section;
    }
    var line = el("p");
    line.append(el("span", SYMBOLS[result.colour] + " " + result.label, "chip chip-" + result.colour), " ");
    if (result.basis === "manual") { line.append(el("span", "MANUAL", "badge-manual")); }
    if (result.basis === "automatic") { line.append(el("span", "AUTOMATIC", "badge-automatic")); }
    section.append(line);
    if (result.latest) {
      section.append(kv("Last result", result.latest.status + " (" + result.latest.check + ")"));
      section.append(kv("Observed", result.latest.observed_at.replace("T", " ").slice(0, 19) + " UTC"));
      section.append(kv("Account / region", (result.latest.account || "unknown") + " / " +
                                             (result.latest.region || "unknown")));
      section.append(kv("Details", result.latest.detail));
      if (result.latest.reference) { section.append(kv("Source", result.latest.reference)); }
      if (result.latest.notes) { section.append(kv("Notes", result.latest.notes)); }
    }
    if (result.note) { section.append(el("p", result.note, "small note")); }
    if (result.history && result.history.length > 1) {
      section.append(el("p", "History (newest first, never overwritten):", "small"));
      var table = el("table", null, "data-table");
      result.history.forEach(function (item) {
        var row = document.createElement("tr");
        row.append(el("td", item.observed_at.replace("T", " ").slice(0, 16)),
                   el("td", item.source === "manual" ? "MANUAL" : item.source), el("td", item.status),
                   el("td", item.notes || item.detail));
        table.append(row);
      });
      section.append(table);
    }
    return section;
  }

  function troubleshootingSection(lines) {
    var section = el("div", null, "section");
    section.append(el("h3", "Troubleshooting"));
    var list = el("ul", null, "small");
    lines.forEach(function (text) { list.append(el("li", text)); });
    section.append(list);
    return section;
  }

  function manualForm(element) {
    var section = el("div", null, "section");
    section.append(el("h3", "Add a MANUAL verification"));
    section.append(el("p", "Your own attested check (for example in the AWS Console). It is always shown as MANUAL, " +
                           "never as automatic evidence.", "small muted"));
    var outcome = document.createElement("select");
    [["pass", "Verified (pass)"], ["partial", "Partial"], ["fail", "Failed"]].forEach(function (choice) {
      var option = el("option", choice[1]);
      option.value = choice[0];
      outcome.append(option);
    });
    var when = document.createElement("input");
    when.type = "datetime-local";
    var notes = document.createElement("textarea");
    notes.maxLength = state.catalog.limits.notes;
    notes.placeholder = "What you checked, where, and what you saw.";
    var button = el("button", "Record manual verification");
    button.type = "button";
    button.addEventListener("click", function () {
      if (state.dirty) { say("Save the diagram first.", true); return; }
      var body = { element_id: element.id(), outcome: outcome.value, notes: notes.value };
      if (when.value) { body.observed_at = new Date(when.value).toISOString(); }
      api("POST", "/api/architecture/diagrams/" + state.diagramId + "/manual", body).then(function () {
        say("Manual verification recorded.");
        return loadEvidence();
      }).catch(function (error) { say(error.message, true); });
    });
    section.append(field("Result", outcome), field("When you checked (your local time; empty = now)", when),
                   field("Evidence notes (required)", notes), button);
    return section;
  }

  function nodeDetails(node) {
    var info = typeInfo(node.data("type"));
    var result = resultFor(node);
    details.append(el("h3", info.label));
    if (design) {
      details.append(field("Label", textInput(node.data("label"), state.catalog.limits.label, function (value) {
        node.data("label", value); node.data("unsaved", true); paint(node); markDirty();
      })));
      details.append(field("Identifier: " + info.identifier, textInput(node.data("identifier"), 512, function (value) {
        node.data("identifier", value.trim()); node.data("unsaved", true); paint(node); markDirty();
      })));
      var parent = document.createElement("select");
      parent.append(el("option", "(not inside a VPC or subnet)"));
      parent.firstChild.value = "";
      // Any VPC or subnet box, except this box itself and boxes inside it (no loops).
      cy.nodes().forEach(function (other) {
        if (other.id() !== node.id() && typeInfo(other.data("type")).container && !other.ancestors().contains(node)) {
          var option = el("option", typeInfo(other.data("type")).label + ": " +
                                    (other.data("label") || other.data("identifier") || other.id()));
          option.value = other.id();
          parent.append(option);
        }
      });
      parent.value = node.data("parent") || "";
      parent.addEventListener("change", function () {
        // move() replaces the box with a new element: keep working with the NEW one, or later edits are lost.
        var moved = node.move({ parent: parent.value || null });
        var current = moved.length ? moved[0] : cy.getElementById(node.id());
        cy.$(":selected").unselect();
        current.select();
        markDirty();
        showDetails(current);
      });
      details.append(field("Inside", parent));
      var remove = el("button", "Delete this box");
      remove.type = "button";
      remove.addEventListener("click", function () {
        var children = node.children();
        if (children.length > 0) {
          // Removing a group would remove everything inside it: move the boxes out first, after asking.
          if (!window.confirm("Delete this " + info.label + " box? The " + children.length +
                              " box(es) inside it are kept and moved out of it.")) { return; }
          children.move({ parent: node.data("parent") || null });
          node = cy.getElementById(node.id());
        }
        node.remove();
        details.replaceChildren();
        markDirty();
      });
      details.append(remove);
    } else {
      details.append(kv("Label", node.data("label")), kv("Identifier", node.data("identifier")));
    }
    details.append(kv("Evidence key", result ? result.key : "(save first)"));
    details.append(evidenceSection(result));
    if (design && info.verify_now && node.data("identifier") && node.data("identifier") !== "metrics") {
      var section = el("div", null, "section");
      section.append(el("h3", "Verify Now (read-only)"));
      section.append(el("p", "One metadata check of this resource. Nothing is created, changed, sent, or invoked, " +
                             "and no secret value is read. It checks the box only, never an arrow.", "small muted"));
      var verify = el("button", "Verify Now");
      verify.type = "button";
      verify.addEventListener("click", function () {
        if (state.dirty) { say("Save the diagram first.", true); return; }
        verify.disabled = true;
        say("Checking…");
        api("POST", "/api/architecture/diagrams/" + state.diagramId + "/verify", { element_id: node.id() })
          .then(function (outcome) { say("Verify Now: " + outcome.status + ". " + outcome.detail); return loadEvidence(); })
          .catch(function (error) { say(error.message, true); })
          .then(function () { verify.disabled = false; });
      });
      section.append(verify);
      details.append(section);
    }
    details.append(troubleshootingSection([
      "Check that the identifier exactly matches the resource in the AWS Console (" + info.identifier + ").",
      "Use Verify Now or Check AWS environment. Access Denied: compare the role with deploy/iam-ec2-healthcheck-policy.json.",
      "A box only shows whether this resource answered a check. Its arrows need their own evidence."
    ]));
    if (design) { details.append(manualForm(node)); }
  }

  function edgeDetails(edge) {
    var operations = state.catalog.operations;
    var source = edge.source(), target = edge.target();
    var spec = operations[edge.data("operation")];
    var result = resultFor(edge);
    details.append(el("h3", (source.data("label") || typeInfo(source.data("type")).label) + " → " +
                            (target.data("label") || typeInfo(target.data("type")).label)));
    if (design) {
      var select = document.createElement("select");
      Object.keys(operations).forEach(function (opId) {
        var op = operations[opId];
        if (op.sources.indexOf(source.data("type")) >= 0 && op.targets.indexOf(target.data("type")) >= 0) {
          var option = el("option", op.label + (op.automatic ? " — automatic evidence possible" : " — design only"));
          option.value = opId;
          select.append(option);
        }
      });
      select.value = edge.data("operation");
      select.addEventListener("change", function () {
        edge.data("operation", select.value);
        edge.data("label", operations[select.value].label);
        edge.data("unsaved", true); paint(edge); markDirty(); showDetails(edge);
      });
      details.append(field("Operation", select));
      details.append(field("Label", textInput(edge.data("label"), state.catalog.limits.label, function (value) {
        edge.data("label", value); paint(edge); markDirty();
      })));
      var remove = el("button", "Delete this arrow");
      remove.type = "button";
      remove.addEventListener("click", function () { edge.remove(); details.replaceChildren(); markDirty(); });
      details.append(remove);
    } else {
      details.append(kv("Operation", spec.label));
    }
    details.append(kv("Evidence key", result ? result.key : "(save first)"));
    details.append(kv("Evidence proves", spec.proves || "No automatic check exists for this operation."));
    details.append(kv("Does NOT prove", spec.not_proves));
    details.append(kv("IAM requirement", spec.iam));
    details.append(evidenceSection(result));
    if (result && result.context) {
      var context = el("div", null, "section");
      context.append(el("h3", "Box checks (context only)"));
      context.append(el("p", "These checks are about the two resources. They do NOT prove this operation.",
                         "small muted"));
      result.context.forEach(function (item) {
        var node = cy.getElementById(item.box);
        context.append(kv(node.data("label") || typeInfo(node.data("type")).label, item.label +
                          (item.basis === "manual" ? " (MANUAL)" : "")));
      });
      details.append(context);
    }
    details.append(troubleshootingSection([
      "How to produce evidence: " + spec.how,
      "If it failed, check the IAM requirement above and the details of the latest result.",
      "Both boxes need the exact identifiers of the real resources; otherwise no evidence can match."
    ]));
    if (design) { details.append(manualForm(edge)); }
  }

  function showDiagramSettings() {
    details.replaceChildren();
    if (!state.diagramId) { return; }
    details.append(el("h3", "Diagram settings"));
    if (design) {
      details.append(field("Name", textInput(state.name, 80, function (value) { state.name = value; markDirty(); })));
      details.append(field("AWS account (12 digits)", textInput(state.scope.account, 12, function (value) {
        state.scope.account = value.trim(); markDirty();
      })));
      details.append(field("Region, for example ap-south-1", textInput(state.scope.region, 30, function (value) {
        state.scope.region = value.trim(); markDirty();
      })));
      details.append(el("p", "Only evidence from this account and region can colour this diagram. " +
                             "Save to apply.", "small muted"));
    } else {
      details.append(kv("Name", state.name), kv("AWS account", state.scope.account), kv("Region", state.scope.region));
    }
    if (state.evidence && state.evidence.scope_warning) {
      details.append(el("p", state.evidence.scope_warning, "small note"));
    }
    if (state.evidence && state.evidence.unattributed.total > 0) {
      var section = el("div", null, "section");
      section.append(el("h3", "Unattributed evidence"));
      section.append(el("p", "Results whose exact resource, account, or region could not be established. They are " +
                             "kept in the history but never shown on a box.", "small muted"));
      state.evidence.unattributed.recent.forEach(function (item) {
        section.append(kv(item.observed_at.replace("T", " ").slice(0, 16), item.check + ": " + item.status));
      });
      details.append(section);
    }
    details.append(el("p", "Select a box or an arrow to see its configuration, IAM requirements, verification " +
                           "evidence, and troubleshooting.", "small muted"));
  }

  function showDetails(element) {
    details.replaceChildren();
    if (element.isNode()) { nodeDetails(element); } else { edgeDetails(element); }
  }

  // ---------------------------------------------------------------- editing (Design mode only)

  function defaultOperation(sourceType, targetType) {
    var operations = state.catalog.operations;
    var allowed = Object.keys(operations).filter(function (opId) {
      return operations[opId].sources.indexOf(sourceType) >= 0 && operations[opId].targets.indexOf(targetType) >= 0;
    });
    var automatic = allowed.filter(function (opId) { return operations[opId].automatic; });
    return automatic[0] || allowed.filter(function (opId) { return opId !== "custom"; })[0] || "custom";
  }

  function addBox(type) {
    if (!state.diagramId) { say("Create a diagram first.", true); return; }
    var extent = cy.extent();
    var data = { id: randomId("n"), type: type, identifier: "", label: "", unsaved: true };
    if (typeInfo(type).icon) { data.icon = typeInfo(type).icon; }
    var node = cy.add({ group: "nodes", data: data,
                        position: { x: (extent.x1 + extent.x2) / 2 + Math.random() * 40,
                                    y: (extent.y1 + extent.y2) / 2 + Math.random() * 40 } });
    paint(node);
    cy.$(":selected").unselect();
    node.select();
    showDetails(node);
    markDirty();
  }

  function toggleConnect() {
    state.connecting = !state.connecting;
    cy.nodes().removeClass("connect-source");
    state.connectSource = null;
    document.getElementById("btn-connect").textContent = state.connecting ? "Cancel connect" : "Connect";
    say(state.connecting ? "Connect: click the SOURCE box." : "");
  }

  function handleConnectTap(node) {
    if (!state.connectSource) {
      state.connectSource = node;
      node.addClass("connect-source");
      say("Connect: now click the TARGET box.");
      return;
    }
    if (node.id() === state.connectSource.id()) { return; }
    var operation = defaultOperation(state.connectSource.data("type"), node.data("type"));
    var edge = cy.add({ group: "edges", data: { id: randomId("e"), source: state.connectSource.id(), target: node.id(),
                                                operation: operation, label: state.catalog.operations[operation].label,
                                                unsaved: true } });
    paint(edge);
    toggleConnect();
    cy.$(":selected").unselect();
    edge.select();
    showDetails(edge);
    markDirty();
  }

  function buildPalette() {
    var list = document.getElementById("palette-list");
    var search = document.getElementById("palette-search");
    function render() {
      list.replaceChildren();
      var query = search.value.toLowerCase();
      Object.keys(state.catalog.types).sort(function (a, b) {
        return state.catalog.types[a].label.localeCompare(state.catalog.types[b].label);
      }).forEach(function (type) {
        var label = state.catalog.types[type].label;
        if (query && label.toLowerCase().indexOf(query) < 0) { return; }
        var button = el("button", "+ " + label, "palette-item");
        button.type = "button";
        button.addEventListener("click", function () { addBox(type); });
        list.append(button);
      });
    }
    search.addEventListener("input", render);
    render();
  }

  function exportJson() {
    var blob = new Blob([JSON.stringify({ name: state.name, body: bodyFromCanvas() }, null, 2)],
                        { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = (state.name || "architecture").replace(/[^A-Za-z0-9_-]+/g, "_") + ".json";
    document.body.append(link);  // Some browsers only download links that are part of the page.
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
  }

  function importJson(file) {
    if (!state.diagramId) { say("Create or open a diagram first; the import replaces its drawing.", true); return; }
    if (file.size > 200000) { say("The file is too large.", true); return; }
    file.text().then(function (text) {
      var data = JSON.parse(text);
      var body = data.body || data;
      if (!Array.isArray(body.nodes) || !Array.isArray(body.edges)) { throw new Error("Not an architecture file."); }
      state.evidence = null;
      canvasFromBody(body);
      cy.elements().forEach(function (element) { element.data("unsaved", true); paint(element); });
      markDirty();
      say("Imported. Check it, then Save (the server validates every box and arrow).");
    }).catch(function (error) { say("Import failed: " + error.message, true); });
  }

  // ---------------------------------------------------------------- events

  cy.on("tap", "node", function (event) {
    if (state.connecting) { handleConnectTap(event.target); return; }
    showDetails(event.target);
  });
  cy.on("tap", "edge", function (event) { if (!state.connecting) { showDetails(event.target); } });
  cy.on("tap", function (event) { if (event.target === cy && !state.connecting) { showDiagramSettings(); } });
  cy.on("dragfree", "node", function () { if (design) { markDirty(); } });

  function on(id, handler) {
    var element = document.getElementById(id);
    if (element) { element.addEventListener("click", handler); }
  }
  on("btn-layout", runLayout);
  on("btn-fit", function () { cy.fit(undefined, 40); });
  on("btn-zoom-in", function () { cy.zoom({ level: cy.zoom() * 1.2, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } }); });
  on("btn-zoom-out", function () { cy.zoom({ level: cy.zoom() / 1.2, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } }); });
  on("btn-refresh", function () {
    loadEvidence(false).then(function () {
      say(state.dirty ? "Evidence refreshed for the SAVED diagram. Save to check your changes." : "Evidence refreshed.");
    });
  });
  on("btn-new", function () { createDiagram("blank"); });
  on("btn-starter", function () { createDiagram("starter"); });
  on("btn-save", saveDiagram);
  on("btn-connect", toggleConnect);
  on("btn-export", exportJson);
  on("btn-delete-diagram", function () {
    if (!state.diagramId || !window.confirm("Delete the drawing \"" + state.name + "\"? Evidence history is kept.")) { return; }
    api("DELETE", "/api/architecture/diagrams/" + state.diagramId).then(function () {
      state.diagramId = null;
      cy.elements().remove();
      details.replaceChildren();
      return loadDiagramList();
    }).catch(function (error) { say(error.message, true); });
  });
  var importInput = document.getElementById("import-file");
  if (importInput) {
    importInput.addEventListener("change", function () {
      if (importInput.files[0]) { importJson(importInput.files[0]); }
      importInput.value = "";
    });
  }
  document.getElementById("diagram-select").addEventListener("change", function (event) {
    if (state.dirty && !window.confirm("Discard unsaved changes?")) {
      event.target.value = String(state.diagramId);
      return;
    }
    loadDiagram(event.target.value);
  });
  window.addEventListener("beforeunload", function (event) {
    if (state.dirty) { event.preventDefault(); event.returnValue = ""; }
  });

  // Fresh evidence every 30 seconds while the page is visible, for at most 10 minutes per page view.
  window.setInterval(function () {
    if (!document.hidden && state.diagramId && Date.now() - state.openedAt < 10 * 60 * 1000) { loadEvidence(true); }
  }, 30000);

  // ---------------------------------------------------------------- start

  api("GET", "/api/architecture/catalog").then(function (catalog) {
    state.catalog = catalog;
    if (design) { buildPalette(); }
    return loadDiagramList();
  }).catch(function (error) { say(error.message, true); });
})();
