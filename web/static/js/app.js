// app.js -- v3.3  fixed & verified

var pieChart = null;
var goalsChart = null;
var taskIdCounter = 0;
var currentTeamTab = "national";
var selectedHome = "";
var selectedAway = "";
var dataAvailable = false;

// ======================================================================
// Init
// ======================================================================
async function init() {
    // 立即填充下拉框
    filterTeams("home");
    filterTeams("away");
    try {
        var r = await fetch("/api/status");
        var s = await r.json();
        dataAvailable = s.data_available;
        if (!dataAvailable) {
            showDataBanner(s.fetch_errors || []);
        }
    } catch (e) {}
}
function showDataBanner(errors) {
    var el = document.createElement("div");
    el.className = "card data-banner";
    el.innerHTML =
        "<h3>⚠️ 未获取到线上比赛数据</h3>" +
        "<p class='muted'>数据源无法连接：" + (errors || ["网络受限"]).join("、") + "</p>" +
        "<p class='muted'>您可以手动输入赔率后直接预测，或点击刷新数据重试。</p>";
    var main = document.querySelector("main");
    main.insertBefore(el, main.firstChild);
}

// ======================================================================
// Tab & Team Selection
// ======================================================================
function switchTeamTab(btn, tab) {
    document.querySelectorAll("#teamTabBar .tab").forEach(function (t) { t.classList.remove("active"); });
    btn.classList.add("active");
    currentTeamTab = tab;
    filterTeams("home");
    filterTeams("away");
}

function getTeamsForTab() {
    if (currentTeamTab === "national") return NATIONAL_TEAMS || [];
    if (currentTeamTab === "club") return CLUB_TEAMS || [];
    return ALL_TEAMS || [];
}

var activeIdx = { home: -1, away: -1 };

function filterTeams(side) {
    var input = document.getElementById(side + "Search");
    var dropdown = document.getElementById(side + "Dropdown");
    var query = input.value.trim().toLowerCase();
    var teams = getTeamsForTab();

    var filtered = query
        ? teams.filter(function (t) { return t.toLowerCase().indexOf(query) !== -1; })
        : teams;

    var seen = {}; filtered = filtered.filter(function(t) { if (seen[t]) return false; seen[t] = true; return true; }); 
    var show = filtered.slice(0, 40);
    activeIdx[side] = -1;
    
    var sel = side === "home" ? selectedHome : selectedAway;
    var html = "";
    for (var i = 0; i < show.length; i++) {
        var t = show[i];
        var display = t;
        if (query) {
            var regex = new RegExp("(" + escRegex(query) + ")", "gi");
            display = t.replace(regex, "<mark>$1</mark>");
        }
        var cls = t === sel ? "dropdown-item selected" : "dropdown-item";
        html += "<div class='" + cls + "' onclick=\"selectTeam('" + side + "','" + escHtml(t) + "')\">"
             + display + "</div>";
    }
    if (show.length === 0) {
        html = "<div class='dropdown-item muted'>无匹配</div>";
    }
    dropdown.innerHTML = html;
    dropdown.style.display = "block";

}

function selectTeam(side, team) {
    document.getElementById(side + "Search").value = team;
    document.getElementById(side + "Dropdown").style.display = "none";
    activeIdx[side] = -1;
    if (side === "home") selectedHome = team;
    else selectedAway = team;
    updateSelected();
    // Show clear indicator
    var clearBtn = document.getElementById(side + "Clear");
    if (clearBtn) clearBtn.style.display = "flex";
}

function clearTeam(side) {
    document.getElementById(side + "Search").value = "";
    document.getElementById(side + "Dropdown").style.display = "none";
    if (side === "home") selectedHome = "";
    else selectedAway = "";
    updateSelected();
    var clearBtn = document.getElementById(side + "Clear");
    if (clearBtn) clearBtn.style.display = "none";
    document.getElementById(side + "Search").focus();
}

function updateSelected() {
    if (selectedHome && selectedAway) {
        document.getElementById("selectedTeams").style.display = "block";
        document.getElementById("selHome").textContent = selectedHome;
        document.getElementById("selAway").textContent = selectedAway;
        document.getElementById("searchBtn").disabled = false;
    } else {
        document.getElementById("selectedTeams").style.display = "none";
        document.getElementById("searchBtn").disabled = true;
    }
}

// Dropdown item click (event delegation on document)
document.addEventListener("mousedown", function (e) {
    var item = e.target.closest(".dropdown-item");
    if (!item || item.classList.contains("muted")) return;
    
    var team = item.getAttribute("data-team");
    var side = item.getAttribute("data-side");
    if (team && side) {
        e.preventDefault(); // prevent input blur
        selectTeam(side, team);
    }
});

// Click outside picker → close dropdowns
document.addEventListener("click", function (e) {
    if (!e.target.closest(".team-select-box")) {
        document.getElementById("homeDropdown").style.display = "none";
        document.getElementById("awayDropdown").style.display = "none";
        activeIdx.home = -1;
        activeIdx.away = -1;
    }
});

// Keyboard navigation for team search inputs
document.addEventListener("keydown", function (e) {
    var side = null;
    if (e.target.id === "homeSearch") side = "home";
    else if (e.target.id === "awaySearch") side = "away";
    else return;
    
    var dropdown = document.getElementById(side + "Dropdown");
    if (dropdown.style.display !== "block") return;
    
    var items = dropdown.querySelectorAll(".dropdown-item:not(.muted)");
    if (items.length === 0) return;
    
    if (e.key === "ArrowDown") {
        e.preventDefault();
        activeIdx[side] = Math.min(activeIdx[side] + 1, items.length - 1);
        updateActiveItem(items, side);
    } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIdx[side] = Math.max(activeIdx[side] - 1, 0);
        updateActiveItem(items, side);
    } else if (e.key === "Enter") {
        e.preventDefault();
        if (activeIdx[side] >= 0 && activeIdx[side] < items.length) {
            var it = items[activeIdx[side]];
            var t = it.getAttribute("data-team");
            var s = it.getAttribute("data-side");
            if (t && s) selectTeam(s, t);
        }
    } else if (e.key === "Escape") {
        dropdown.style.display = "none";
        activeIdx[side] = -1;
    }
});

function updateActiveItem(items, side) {
    items.forEach(function (item, i) {
        if (i === activeIdx[side]) {
            item.classList.add("active");
            item.scrollIntoView({ block: "nearest" });
        } else {
            item.classList.remove("active");
        }
    });
}

// ======================================================================
// Search
// ======================================================================
async function searchMatches() {
    if (!selectedHome || !selectedAway) return;

    var list = document.getElementById("matchList");
    list.innerHTML = "<div class='loading-text'>搜索中...</div>";
    document.getElementById("matchResults").style.display = "block";
    document.getElementById("confirmPanel").style.display = "none";
    document.getElementById("results").style.display = "none";

    try {
        var r = await fetch("/api/search_matches?team_a=" + encodeURIComponent(selectedHome)
                          + "&team_b=" + encodeURIComponent(selectedAway));
        var data = await r.json();
        var matches = data.matches || [];
        var aMatches = data.team_a_matches || [];
        var bMatches = data.team_b_matches || [];
        var html = "";

        if (matches.length > 0) {
            html += "<div class='section-label'>直接交锋</div>";
            matches.forEach(function (m) {
                var isHistory = m.home_goals !== undefined && m.home_goals !== null;
                var score = isHistory ? " (" + m.home_goals + "-" + m.away_goals + ")" : "";
                var odds = m.home_odds ? "<span>" + m.home_odds + "</span><span>" + m.draw_odds + "</span><span>" + m.away_odds + "</span>" : "<span>赔率未获取</span>";
                html += "<div class='match-card' onclick=\"selectMatch('"
                     + escHtml(m.home_team) + "','" + escHtml(m.away_team) + "',"
                     + (m.home_odds || 0) + "," + (m.draw_odds || 0) + "," + (m.away_odds || 0) + ",'" + escHtml(m.league || "") + "','" + escHtml(m.venue || "") + "')\">"
                     + "<div class='mc-teams'><span>" + (m.home_team || "?") + "</span>"
                     + "<span class='mc-vs'>vs</span><span>" + (m.away_team || "?") + "</span>" + score + "</div>"
                     + "<div class='mc-meta'>" + (m.league || "") + " " + (m.date || "") + " " + (m.note || "") + "</div>"
                     + "<div class='mc-odds'>" + odds + "</div></div>";
            });
        }

        if (aMatches.length > 0) {
            html += "<div class='section-label'>" + selectedHome + " 近期比赛</div>";
            aMatches.forEach(function (m) {
                html += "<div class='match-card small' onclick=\"selectMatch('"
                     + escHtml(m.home_team) + "','" + escHtml(m.away_team) + "',"
                     + (m.home_odds || 0) + "," + (m.draw_odds || 0) + "," + (m.away_odds || 0) + ",'" + escHtml(m.league || "") + "','" + escHtml(m.venue || "") + "')\">"
                     + "<div class='mc-teams'><span>" + (m.home_team || "?") + "</span>"
                     + "<span class='mc-vs'>vs</span><span>" + (m.away_team || "?") + "</span></div>"
                     + "<div class='mc-meta'>" + (m.league || "") + " " + (m.date || "") + "</div></div>";
            });
        }

        if (bMatches.length > 0) {
            html += "<div class='section-label'>" + selectedAway + " 近期比赛</div>";
            bMatches.forEach(function (m) {
                html += "<div class='match-card small' onclick=\"selectMatch('"
                     + escHtml(m.home_team) + "','" + escHtml(m.away_team) + "',"
                     + (m.home_odds || 0) + "," + (m.draw_odds || 0) + "," + (m.away_odds || 0) + ",'" + escHtml(m.league || "") + "','" + escHtml(m.venue || "") + "')\">"
                     + "<div class='mc-teams'><span>" + (m.home_team || "?") + "</span>"
                     + "<span class='mc-vs'>vs</span><span>" + (m.away_team || "?") + "</span></div>"
                     + "<div class='mc-meta'>" + (m.league || "") + " " + (m.date || "") + "</div></div>";
            });
        }

        if (html === "") {
            html = "<div class='empty-state'>"
                 + "<p>未找到 " + selectedHome + " 与 " + selectedAway + " 的比赛数据</p>"
                 + (!data.data_available ? "<p class='muted'>线上数据源暂不可用，可手动输入赔率后直接预测</p>" : "")
                 + "<button class='btn-secondary' onclick='directPredict()'>直接预测</button></div>";
        }

        html += "<div class='match-card direct' onclick='directPredict()'>"
              + "<div class='mc-teams'><span>" + selectedHome + "</span>"
              + "<span class='mc-vs'>vs</span><span>" + selectedAway + "</span></div>"
              + "<div class='mc-meta'>跳过比赛数据，手动输入赔率后预测</div></div>";

        list.innerHTML = html;
    } catch (e) {
        list.innerHTML = "<div class='loading-text'>搜索失败: " + e.message + "</div>";
    }
}

// ======================================================================
// Confirm & Predict
// ======================================================================
function selectMatch(home, away, ho, d_o, ao, league, venue) {
    document.getElementById("confirmHome").textContent = home;
    document.getElementById("confirmAway").textContent = away;
    document.getElementById("homeOdds").value = ho > 0 ? ho : "";
    document.getElementById("drawOdds").value = d_o > 0 ? d_o : "";
    document.getElementById("awayOdds").value = ao > 0 ? ao : "";
    
    var neutralSelect = document.getElementById("neutral");
    var tournamentLeagues = ["世界杯","欧洲杯","美洲杯","欧冠","欧联杯","亚冠","欧国联"];
    var isNeutral = tournamentLeagues.includes(league || "");
    neutralSelect.value = isNeutral ? "true" : "false";
    
    var matchInfo = document.getElementById("matchInfo");
    if (matchInfo) {
        var venueText = venue || (isNeutral ? "中立场地" : (home + " 主场"));
        var leagueTag = league ? '<span style="background:var(--green-bg);color:var(--green);padding:2px 8px;border-radius:4px;font-size:.7rem;font-weight:700">' + league + '</span>' : '';
        matchInfo.innerHTML = leagueTag + ' · ' + venueText + ' · 场地已自动设为: <b>' + (isNeutral ? '中立' : '主队主场') + '</b>';
        matchInfo.style.display = "block";
    }
    
    loadTeamPlayersPanel("home", home);
    loadTeamPlayersPanel("away", away);
    selectedHome = home;
    selectedAway = away;
    document.getElementById("confirmPanel").style.display = "block";
    document.getElementById("results").style.display = "none";
    document.getElementById("confirmPanel").scrollIntoView({ behavior: "smooth" });
}

function directPredict() {
    document.getElementById("confirmHome").textContent = selectedHome;
    document.getElementById("confirmAway").textContent = selectedAway;
    document.getElementById("homeOdds").value = "";
    document.getElementById("drawOdds").value = "";
    document.getElementById("awayOdds").value = "";
    loadTeamPlayersPanel("home", selectedHome);
    loadTeamPlayersPanel("away", selectedAway);
    document.getElementById("confirmPanel").style.display = "block";
    document.getElementById("results").style.display = "none";
    document.getElementById("confirmPanel").scrollIntoView({ behavior: "smooth" });
}

function loadTeamPlayersPanel(side, team) {
    var container = document.getElementById(side + "Players");
    var players = PLAYERS_DATA[team] || {};
    if (Object.keys(players).length === 0) {
        container.innerHTML = "<span class='muted'>无球员数据</span>";
        return;
    }
    var html = "<label class='missing-label'>缺阵球员（勾选排除）：</label><div class='player-grid'>";
    for (var name in players) {
        if (!players.hasOwnProperty(name)) continue;
        var imp = players[name];
        var stars = "";
        for (var s = 0; s < Math.min(5, Math.round(imp / 2)); s++) stars += "★";
        html += "<label class='player-check'><input type='checkbox' value='" + escHtml(name) + "'>"
             + "<span>" + name + "</span><span class='stars'>" + stars + "</span></label>";
    }
    html += "</div>";
    container.innerHTML = html;
}

function getMissingPlayers(side) {
    var container = document.getElementById(side + "Players");
    var checks = container.querySelectorAll("input:checked");
    return Array.from(checks).map(function (c) { return c.value; });
}

// ======================================================================
// Run Prediction
// ======================================================================
async function runPrediction() {
    if (!selectedHome || !selectedAway) { alert("请先选择球队"); return; }
    var taskId = "task_" + (++taskIdCounter);
    var progArea = document.getElementById("progressTrack");
    var btn = document.getElementById("runBtn");
    progArea.style.display = "block";
    btn.disabled = true;
    btn.textContent = "计算中...";
    document.getElementById("results").style.display = "none";
    updateProgress(0, 12, "启动中...");

    var evtSource = new EventSource("/api/progress/" + taskId);
    evtSource.onmessage = function (e) {
        var info = JSON.parse(e.data);
        updateProgress(info.done || 0, info.total || 12, info.current || "");
    };

    var payload = {
        home_team: selectedHome, away_team: selectedAway,
        league: document.getElementById("league").value,
        neutral: document.getElementById("neutral").value === "true",
        home_missing: getMissingPlayers("home"),
        away_missing: getMissingPlayers("away"),
        task_id: taskId,
    };
    var ho = document.getElementById("homeOdds").value;
    var dd = document.getElementById("drawOdds").value;
    var ao = document.getElementById("awayOdds").value;
    if (ho && dd && ao) {
        payload.home_odds = ho;
        payload.draw_odds = dd;
        payload.away_odds = ao;
    }

    try {
        var resp = await fetch("/predict", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        var data = await resp.json();
        if (data.error) { alert(data.error); return; }
        updateProgress(12, 12, "完成");
        await sleep(300);
        renderResults(data);
    } catch (err) {
        alert("请求失败: " + err.message);
    } finally {
        evtSource.close();
        setTimeout(function () {
            progArea.style.display = "none";
            btn.disabled = false;
            btn.textContent = "🚀 开始预测";
        }, 500);
    }
}

function updateProgress(done, total, current) {
    document.getElementById("progressFill").style.width = (done / total * 100) + "%";
    document.getElementById("progressText").textContent = done + "/" + total;
    document.getElementById("progressText").textContent = current;
}

// ======================================================================
// Render
// ======================================================================
function renderResults(data) {
    document.getElementById("resHome").textContent = data.home_team || selectedHome;
    document.getElementById("resAway").textContent = data.away_team || selectedAway;

    var ens = data.ensemble;
    document.querySelector("#probHome .val").textContent = (ens.home_win * 100).toFixed(1) + "%";
    document.querySelector("#probDraw .val").textContent = (ens.draw * 100).toFixed(1) + "%";
    document.querySelector("#probAway .val").textContent = (ens.away_win * 100).toFixed(1) + "%";
    document.querySelector("#showGoals").textContent = ens.expected_total_goals.toFixed(1);
    document.querySelector("#showConf").textContent = data.confidence.toFixed(0) + "%";

    highlightMax(".prob-block", [ens.home_win, ens.draw, ens.away_win]);
    renderPieChart(ens.home_win, ens.draw, ens.away_win);

    var scoresHtml = "";
    var topScores = ens.top_scores || [];
    for (var i = 0; i < Math.min(topScores.length, 5); i++) {
        var score = topScores[i][0];
        var prob = topScores[i][1];
        scoresHtml += "<div class='score-chip'><div class='score'>" + score
                   + "</div><div class='prob'>" + (prob * 100).toFixed(1) + "%</div></div>";
    }
    document.getElementById("topScores").innerHTML = scoresHtml;

    renderModelsTable(data.predictions, ens.weights || {});

    var gd = (data.predictions.monte_carlo && data.predictions.monte_carlo.total_goals_dist)
          || (data.predictions.poisson && data.predictions.poisson.total_goals_dist)
          || {};
    renderGoalsChart(gd);

    var ou = getOverUnder(data.predictions);
    document.getElementById("overUnder").innerHTML =
        "<div class='ou-badge under'><div class='label'>Under 2.5</div><div class='value'>"
        + (ou.under * 100).toFixed(1) + "%</div></div>"
        + "<div class='ou-badge over'><div class='label'>Over 2.5</div><div class='value'>"
        + (ou.over * 100).toFixed(1) + "%</div></div>";

    // HTFT display
    var htftGrid = document.getElementById("htftGrid");
    if (htftGrid && data.htft && data.htft.top) {
        var htftHtml = "";
        var htftItems = data.htft.top;
        for (var i = 0; i < htftItems.length; i++) {
            var label = htftItems[i][0];
            var prob = (htftItems[i][1] * 100).toFixed(1);
            var cls = prob > 20 ? "htft-cell high" : "htft-cell";
            htftHtml += '<div class="' + cls + '"><div class="label">' + label + '</div><div class="pct">' + prob + '%</div></div>';
        }
        htftGrid.innerHTML = htftHtml;
    }

    
    // 让球胜负
    var handicapGrid = document.getElementById("handicapGrid");
    if (handicapGrid && data.handicap) {
        var hpHtml = "";
        for (var label in data.handicap) {
            if (!data.handicap.hasOwnProperty(label)) continue;
            var hp = data.handicap[label];
            var winPct = (hp.win * 100).toFixed(1);
            var drawPct = hp.draw ? (hp.draw * 100).toFixed(1) : "0.0";
            var losePct = (hp.lose * 100).toFixed(1);
            hpHtml += '<div class="handicap-card">';
            hpHtml += '<div class="hp-spread">' + label + '</div>';
            hpHtml += '<div class="hp-bars"><div class="hp-win" style="width:' + winPct + '%"></div><div class="hp-draw" style="width:' + drawPct + '%"></div><div class="hp-lose" style="width:' + losePct + '%"></div></div>';
            hpHtml += '<div class="hp-vals"><span class="w">让胜 ' + winPct + '%</span><span class="d">走水 ' + drawPct + '%</span><span class="l">让负 ' + losePct + '%</span></div>';
            hpHtml += '</div>';
        }
        handicapGrid.innerHTML = hpHtml;
    }

    document.getElementById("results").style.display = "block";
    document.getElementById("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function highlightMax(selector, values) {
    var blocks = document.querySelectorAll(selector);
    var maxIdx = values.indexOf(Math.max.apply(null, values));
    for (var i = 0; i < blocks.length; i++) {
        blocks[i].classList.remove("lit");
    }
    if (blocks[maxIdx]) blocks[maxIdx].classList.add("lit");
}

function renderPieChart(home, draw, away) {
    var ctx = document.getElementById("pieChart").getContext("2d");
    if (pieChart) pieChart.destroy();
    pieChart = new Chart(ctx, {
        type: "doughnut",
        data: {
            labels: ["主胜", "平局", "客胜"],
            datasets: [{
                data: [home * 100, draw * 100, away * 100],
                backgroundColor: [
                    "rgba(74,222,128,0.7)",
                    "rgba(250,204,21,0.7)",
                    "rgba(248,113,113,0.7)",
                ],
                borderColor: ["#4ade80", "#facc15", "#f87171"],
                borderWidth: 2,
            }],
        },
        options: {
            responsive: true,
            plugins: {
                legend: {
                    position: "bottom",
                    labels: { color: "#94a3b8", padding: 16 },
                },
                tooltip: {
                    callbacks: {
                        label: function (ctx) {
                            return ctx.label + ": " + ctx.raw.toFixed(1) + "%";
                        },
                    },
                },
            },
        },
    });
}

var MODEL_NAMES = {
    poisson: "泊松分布", dixon_coles: "Dixon-Coles", elo: "ELO评级",
    massey: "Massey排名", form: "近期状态", head_to_head: "交锋记录",
    market_odds: "市场赔率", knn_similar: "KNN相似",
    xgboost: "XGBoost", neural_net: "神经网络",
    monte_carlo: "蒙特卡洛", bayesian: "贝叶斯层次",
};

function renderModelsTable(preds, weights) {
    var tbody = document.querySelector("#modelsTable tbody");
    var html = "";
    for (var key in preds) {
        if (!preds.hasOwnProperty(key)) continue;
        var pred = preds[key];
        var w = weights[key] || 0;
        var goals = pred.expected_total_goals ? pred.expected_total_goals.toFixed(1) : "-";
        var cls = pred.status ? "dimmed" : "";
        html += "<tr class='" + cls + "'>"
             + "<td class='model-name'>" + (MODEL_NAMES[key] || key) + "</td>"
             + "<td class='prob-cell h'>" + (pred.home_win * 100).toFixed(1) + "%</td>"
             + "<td class='prob-cell d'>" + (pred.draw * 100).toFixed(1) + "%</td>"
             + "<td class='prob-cell a'>" + (pred.away_win * 100).toFixed(1) + "%</td>"
             + "<td>" + goals + "</td>"
             + "<td>" + (w * 100).toFixed(1) + "%</td>"
             + "</tr>";
    }
    tbody.innerHTML = html;
}

function renderGoalsChart(dist) {
    var ctx = document.getElementById("goalsChart").getContext("2d");
    if (goalsChart) goalsChart.destroy();
    var labels = Object.keys(dist).map(Number);
    var values = Object.values(dist);
    if (!labels.length) return;

    var colors = values.map(function (v) {
        return v > 0.1 ? "rgba(56,189,248,0.6)" : "rgba(56,189,248,0.3)";
    });

    goalsChart = new Chart(ctx, {
        type: "bar",
        data: {
            labels: labels,
            datasets: [{
                label: "概率",
                data: values.map(function (v) { return v * 100; }),
                backgroundColor: colors,
                borderColor: "#38bdf8",
                borderWidth: 1,
                borderRadius: 4,
            }],
        },
        options: {
            responsive: true,
            plugins: { legend: { labels: { color: "#94a3b8" } } },
            scales: {
                x: {
                    title: { display: true, text: "总进球数", color: "#94a3b8" },
                    ticks: { color: "#94a3b8" },
                    grid: { color: "#1e293b" },
                },
                y: {
                    title: { display: true, text: "概率 (%)", color: "#94a3b8" },
                    ticks: { color: "#94a3b8", callback: function (v) { return v.toFixed(1) + "%"; } },
                    grid: { color: "#1e293b" },
                },
            },
        },
    });
}

function getOverUnder(preds) {
    var over = 0, under = 0, cnt = 0;
    for (var key in preds) {
        if (!preds.hasOwnProperty(key)) continue;
        var p = preds[key];
        if (p.over_25 !== undefined && p.under_25 !== undefined) {
            over += p.over_25;
            under += p.under_25;
            cnt++;
        }
    }
    return cnt > 0 ? { over: over / cnt, under: under / cnt } : { over: 0.55, under: 0.45 };
}

// ======================================================================
// Utils
// ======================================================================
function escHtml(s) {
    s = String(s || "");
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function escRegex(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

async function refreshData() {
    var btn = event.target;
    btn.disabled = true;
    btn.textContent = "刷新中...";
    try {
        var r = await fetch("/api/refresh_data");
        var d = await r.json();
        if (d.status === "ok") {
            alert("成功获取 " + d.upcoming + " 场比赛数据！刷新页面查看。");
        } else {
            alert("未获取到数据。\n" + (d.errors || []).join("\n") + "\n\n可手动输入赔率进行预测。");
        }
        location.reload();
    } catch (e) {
        alert("刷新失败: " + e.message);
    }
    btn.disabled = false;
    btn.textContent = "刷新数据";
}


// ======================================================================
// 查看计算过程
// ======================================================================
async function showDebug() {
    if (!selectedHome || !selectedAway) { alert("请先选择球队并预测"); return; }
    var panel = document.getElementById("debugPanel");
    var content = document.getElementById("debugContent");

    if (panel.style.display === "block") {
        panel.style.display = "none";
        return;
    }

    content.innerHTML = "<div class='loading-text'>加载计算过程...</div>";
    panel.style.display = "block";
    panel.scrollIntoView({ behavior: "smooth" });

    try {
        var r = await fetch("/api/debug_predict", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                home_team: selectedHome, away_team: selectedAway,
                neutral: document.getElementById("neutral").value === "true",
            }),
        });
        var d = await r.json();
        renderDebug(d);
    } catch (e) {
        content.innerHTML = "<div class='loading-text'>加载失败: " + e.message + "</div>";
    }
}

function renderDebug(d) {
    var raw = d.raw_data || {};
    var steps = d.calculation_steps || {};
    var outputs = d.model_outputs || {};
    var weights = d.weights || {};

    var h = "";

    // 参考数据
    h += "<div class='debug-section'>";
    h += "<h3>参考数据</h3>";
    h += "<table class='debug-table'>";
    h += "<tr><td>ELO 评分</td><td class='v'>" + (raw.elo_home||"-") + " vs " + (raw.elo_away||"-") + "</td></tr>";
    h += "<tr><td>Massey 评分</td><td class='v'>" + (raw.massey_home||0).toFixed(2) + " vs " + (raw.massey_away||0).toFixed(2) + "</td></tr>";
    h += "<tr><td>攻击力</td><td class='v'>" + (raw.attack_home||1).toFixed(2) + " vs " + (raw.attack_away||1).toFixed(2) + "</td></tr>";
    h += "<tr><td>防守力</td><td class='v'>" + (raw.defense_home||1).toFixed(2) + " vs " + (raw.defense_away||1).toFixed(2) + "</td></tr>";
    var fh = raw.form_home || {}, fa = raw.form_away || {};
    h += "<tr><td>近期状态分</td><td class='v'>" + (fh.form_score||"-") + " vs " + (fa.form_score||"-") + "</td></tr>";
    h += "<tr><td>近期场均积分</td><td class='v'>" + (fh.ppg||"-") + " vs " + (fa.ppg||"-") + "</td></tr>";
    var h2h = raw.h2h || {};
    h += "<tr><td>历史交锋</td><td class='v'>" + (h2h.total_matches||0) + " 场: " + (h2h.a_wins||0) + "胜" + (h2h.draws||0) + "平" + (h2h.b_wins||0) + "负</td></tr>";
    h += "</table></div>";

    // 半全场
    if (d.htft && d.htft.top) {
        h += "<div class='debug-section'>";
        h += "<h3>半全场预测 (半场/全场)</h3>";
        h += "<table class='debug-table'>";
        var htftTop = d.htft.top.slice(0, 9);
        for (var i = 0; i < htftTop.length; i++) {
            var item = htftTop[i];
            h += "<tr><td>" + item[0] + "</td><td class='v'>" + (item[1]*100).toFixed(1) + "%</td></tr>";
        }
        h += "</table></div>";
    }

    // 各模型计算过程
    h += "<div class='debug-section'>";
    h += "<h3>各模型计算过程</h3>";

    var modelNames = {
        poisson: "泊松分布", elo: "ELO 评级", massey: "Massey 排名",
        form: "近期状态", head_to_head: "交锋记录",
        market_odds: "市场赔率", monte_carlo: "蒙特卡洛", bayesian: "贝叶斯层次",
    };

    for (var key in steps) {
        if (!steps.hasOwnProperty(key)) continue;
        var s = steps[key];
        var name = modelNames[key] || key;
        var out = outputs[key] || {};
        var w = weights[key] ? (weights[key]*100).toFixed(1) : "-";

        h += "<div class='debug-model'>";
        h += "<div class='debug-model-header'><b>" + name + "</b> <span class='badge-sm'>权重 " + w + "%</span></div>";
        h += "<div class='debug-model-body'>";

        if (s.formula) h += "<div class='debug-row'><span class='lbl'>公式</span><code>" + s.formula + "</code></div>";
        if (s.lambda_home !== undefined) h += "<div class='debug-row'><span class='lbl'>λ 主队</span><span class='v'>" + s.lambda_home + "</span></div>";
        if (s.lambda_away !== undefined) h += "<div class='debug-row'><span class='lbl'>λ 客队</span><span class='v'>" + s.lambda_away + "</span></div>";
        if (s.elo_diff !== undefined) h += "<div class='debug-row'><span class='lbl'>ELO差</span><span class='v'>" + s.elo_diff.toFixed(0) + "</span></div>";
        if (s.expected_win !== undefined) h += "<div class='debug-row'><span class='lbl'>预期胜率</span><span class='v'>" + (s.expected_win*100).toFixed(1) + "%</span></div>";
        if (s.form_diff !== undefined) h += "<div class='debug-row'><span class='lbl'>状态差</span><span class='v'>" + s.form_diff.toFixed(3) + "</span></div>";
        if (s.record) h += "<div class='debug-row'><span class='lbl'>交锋记录</span><span class='v'>" + s.record + "</span></div>";
        if (s.note) h += "<div class='debug-row'><span class='lbl'>说明</span><span class='v'>" + s.note + "</span></div>";
        if (s.interpretation) h += "<div class='debug-row'><span class='lbl'>解读</span><span class='v'>" + s.interpretation + "</span></div>";

        if (out) {
            h += "<div class='debug-row'><span class='lbl'>输出</span><span class='v'>";
            h += "主胜 " + (out.home_win*100).toFixed(1) + "% | ";
            h += "平局 " + (out.draw*100).toFixed(1) + "% | ";
            h += "客胜 " + (out.away_win*100).toFixed(1) + "%";
            if (out.expected_goals !== undefined) h += " | 进球 " + out.expected_goals;
            h += "</span></div>";
        }

        h += "</div></div>";
    }

    h += "</div>";

    // 融合计算
    var ens = d.ensemble || {};
    h += "<div class='debug-section'>";
    h += "<h3>融合计算</h3>";
    h += "<p>各模型输出 × 权重 → 加权平均</p>";
    h += "<table class='debug-table'>";
    for (var key in outputs) {
        if (!outputs.hasOwnProperty(key)) continue;
        var o = outputs[key];
        var w = weights[key] || 0;
        h += "<tr><td>" + (modelNames[key]||key) + "</td>";
        h += "<td class='v'>" + (o.home_win*100).toFixed(1) + "% × " + (w*100).toFixed(1) + "% = " + (o.home_win*w*100).toFixed(2) + "</td></tr>";
    }
    h += "<tr style='border-top:2px solid var(--accent)'><td><b>融合结果</b></td>";
    h += "<td class='v'><b>主胜 " + (ens.home_win*100).toFixed(1) + "% | 平局 " + (ens.draw*100).toFixed(1) + "% | 客胜 " + (ens.away_win*100).toFixed(1) + "% | 进球 " + ens.expected_total_goals + "</b></td></tr>";
    h += "</table></div>";

    document.getElementById("debugContent").innerHTML = h;
}

// Keyboard shortcut
document.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.ctrlKey) runPrediction();
});



// ======================================================================
// Sidebar - 近期比赛
// ======================================================================
async function loadSidebar() {
    var list = document.getElementById('sidebarList');
    list.innerHTML = '<div class="loading-text">加载中...</div>';
    try {
        var r = await fetch('/api/upcoming');
        var d = await r.json();
        var matches = d.upcoming || [];
        if (matches.length === 0) {
            list.innerHTML = '<div class="empty-state">暂无近期比赛</div>';
            return;
        }
        var html = '';
        for (var i = 0; i < matches.length; i++) {
            var m = matches[i];
            var home = m.home_team || '?';
            var away = m.away_team || '?';
            var league = m.league || '';
            var ho = m.home_odds ? '<span class="ho">' + m.home_odds + '</span>' : '';
            var do_s = m.draw_odds ? '<span class="do">' + m.draw_odds + '</span>' : '';
            var ao = m.away_odds ? '<span class="ao">' + m.away_odds + '</span>' : '';
            var oddStr = (ho || do_s || ao) ? '<div class="sm-odds">' + ho + ' ' + do_s + ' ' + ao + '</div>' : '';
            var leagueTag = league ? '<span class="sm-league">' + escHtml(league) + '</span>' : '';
            var venue = m.venue ? escHtml(m.venue) : '';
            html += '<div class="sidebar-match" onclick="sidebarPredict(\'' + escHtml(home) + '\',\'' + escHtml(away) + '\',\'' + escHtml(league) + '\',' + (m.home_odds||0) + ',' + (m.draw_odds||0) + ',' + (m.away_odds||0) + ')">';
            html += '<div class="sm-teams">' + escHtml(home) + ' <span style="color:var(--text-muted);font-size:.7rem">vs</span> ' + escHtml(away) + '</div>';
            html += '<div class="sm-info">' + leagueTag + ' ' + venue + '</div>';
            html += oddStr;
            html += '</div>';
        }
        list.innerHTML = html;
    } catch (e) {
        list.innerHTML = '<div class="loading-text">加载失败: ' + e.message + '</div>';
    }
}

function sidebarPredict(home, away, league, ho, draw_o, ao) {
    // Set teams
    selectedHome = home;
    selectedAway = away;
    document.getElementById('homeSearch').value = home;
    document.getElementById('awaySearch').value = away;
    updateSelected();
    
    // Set league
    var leagueSelect = document.getElementById('league');
    if (league) {
        for (var i = 0; i < leagueSelect.options.length; i++) {
            if (leagueSelect.options[i].value === league) {
                leagueSelect.selectedIndex = i;
                break;
            }
        }
    }
    
    // Auto-set neutral for tournament leagues
    var tournamentLeagues = ['世界杯','欧洲杯','美洲杯','欧冠','欧联杯','亚冠','欧国联'];
    var neutralSelect = document.getElementById('neutral');
    neutralSelect.value = tournamentLeagues.includes(league) ? 'true' : 'false';
    
    // Fill confirm panel
    document.getElementById('confirmHome').textContent = home;
    document.getElementById('confirmAway').textContent = away;
    document.getElementById('homeOdds').value = ho > 0 ? ho : '';
    document.getElementById('drawOdds').value = draw_o > 0 ? draw_o : '';
    document.getElementById('awayOdds').value = ao > 0 ? ao : '';
    
    // Show match info
    var matchInfo = document.getElementById('matchInfo');
    if (matchInfo) {
        var isNeutral = tournamentLeagues.includes(league || '');
        var venueText = isNeutral ? '中立场地' : (home + ' 主场');
        var leagueTag = league ? '<span style="background:var(--green-bg);color:var(--green);padding:2px 8px;border-radius:4px;font-size:.7rem;font-weight:700">' + escHtml(league) + '</span>' : '';
        matchInfo.innerHTML = leagueTag + ' · ' + venueText;
        matchInfo.style.display = 'block';
    }
    
    loadTeamPlayersPanel('home', home);
    loadTeamPlayersPanel('away', away);
    
    // Show confirm panel and scroll
    document.getElementById('confirmPanel').style.display = 'block';
    document.getElementById('results').style.display = 'none';
    document.getElementById('confirmPanel').scrollIntoView({ behavior: 'smooth' });
    
    // Auto run prediction
    runPrediction();
}

// Load sidebar on init
var origInit = init;
init = async function() {
    await origInit();
    loadSidebar();
};

// Start
init();
