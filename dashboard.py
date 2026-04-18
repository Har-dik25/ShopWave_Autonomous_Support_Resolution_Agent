"""
ShopWave Agent — Live Web Dashboard
=====================================
A single-file web application (no dependencies beyond stdlib) that:
1. Runs the agent on all 20 tickets
2. Serves an interactive HTML dashboard at http://localhost:8080
3. Shows real-time results, reasoning traces, failure recovery, and self-evaluation

Launch:  python dashboard.py
View:    http://localhost:8080
"""

import json
import os
import sys
import time
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_manager import get_all_tickets
from agent import resolve_ticket
from tools import get_audit_log, ENABLE_FAILURE_SIMULATION
from evaluator import evaluate_results

# Global state for the dashboard
DASHBOARD_STATE = {
    "status": "initializing",
    "reports": [],
    "tickets": [],
    "audit_log": [],
    "evaluation": {},
    "summary": {},
    "elapsed": 0,
}


def run_agent():
    """Run the agent and populate DASHBOARD_STATE."""
    DASHBOARD_STATE["status"] = "running"
    tickets = get_all_tickets()
    DASHBOARD_STATE["tickets"] = tickets
    total = len(tickets)
    reports = [None] * total
    num_workers = min(8, total)

    start = time.time()
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        future_to_idx = {
            executor.submit(resolve_ticket, t): i for i, t in enumerate(tickets)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                reports[idx] = future.result()
            except Exception as e:
                reports[idx] = {
                    "ticket_id": tickets[idx]["ticket_id"],
                    "resolution": "error", "category": "unknown",
                    "urgency": "high", "flags": ["processing_error"],
                    "confidence_score": 0.0, "tools_used": [],
                    "tool_call_count": 0, "reasoning_steps": [{"thought": f"ERROR: {e}"}],
                    "total_steps": 1, "min_3_tools_met": False,
                    "explainability": {"decisions": []}, "error": str(e),
                }

    elapsed = time.time() - start
    audit_log = get_audit_log()

    # Self-evaluation
    evaluation = evaluate_results(tickets, reports)

    # Summary
    resolved = sum(1 for r in reports if r["resolution"] in ("resolved", "resolved_declined"))
    escalated = sum(1 for r in reports if r["resolution"] == "escalated")
    awaiting = sum(1 for r in reports if r["resolution"] == "awaiting_customer_info")
    failures = [e for e in audit_log if "failure" in e.get("tool", "")]
    recoveries = [e for e in audit_log if e.get("recovered_from_failure")]

    DASHBOARD_STATE.update({
        "status": "complete",
        "reports": reports,
        "audit_log": audit_log,
        "evaluation": evaluation,
        "elapsed": round(elapsed, 3),
        "summary": {
            "total": total,
            "resolved": resolved,
            "escalated": escalated,
            "awaiting": awaiting,
            "autonomous_rate": round(resolved / total * 100, 1),
            "avg_confidence": round(sum(r["confidence_score"] for r in reports) / total * 100),
            "chain_met": sum(1 for r in reports if r.get("min_3_tools_met")),
            "failures": len(failures),
            "recoveries": len(recoveries),
            "eval_accuracy": round(evaluation.get("weighted_score", 0) * 100),
        },
    })


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ShopWave Agent — Live Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0a0e17;
  --surface: #111827;
  --surface2: #1a2332;
  --border: #1e293b;
  --text: #e2e8f0;
  --text-dim: #94a3b8;
  --accent: #3b82f6;
  --green: #10b981;
  --yellow: #f59e0b;
  --red: #ef4444;
  --purple: #8b5cf6;
  --cyan: #06b6d4;
  --pink: #ec4899;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Inter', -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  min-height: 100vh;
}
.container { max-width: 1400px; margin: 0 auto; padding: 24px; }

/* Header */
.header {
  text-align: center;
  padding: 40px 0 30px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 32px;
}
.header h1 {
  font-size: 2.2rem;
  font-weight: 800;
  background: linear-gradient(135deg, var(--accent), var(--purple), var(--pink));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  margin-bottom: 8px;
}
.header .subtitle {
  color: var(--text-dim);
  font-size: 0.95rem;
  font-weight: 400;
}
.badge-row { display: flex; gap: 12px; justify-content: center; margin-top: 16px; flex-wrap: wrap; }
.badge {
  padding: 4px 14px;
  border-radius: 20px;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.badge-blue { background: rgba(59,130,246,0.15); color: var(--accent); border: 1px solid rgba(59,130,246,0.3); }
.badge-green { background: rgba(16,185,129,0.15); color: var(--green); border: 1px solid rgba(16,185,129,0.3); }
.badge-purple { background: rgba(139,92,246,0.15); color: var(--purple); border: 1px solid rgba(139,92,246,0.3); }

/* Stats Grid */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 32px;
}
.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  text-align: center;
  transition: transform 0.2s, border-color 0.2s;
}
.stat-card:hover { transform: translateY(-2px); border-color: var(--accent); }
.stat-value { font-size: 2rem; font-weight: 800; margin-bottom: 4px; }
.stat-label { font-size: 0.8rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 1px; }

/* Sections */
.section {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  margin-bottom: 24px;
  overflow: hidden;
}
.section-header {
  padding: 16px 24px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  font-size: 1.1rem;
}

/* Ticket Cards */
.ticket-list { padding: 12px; }
.ticket-card {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 10px;
  margin-bottom: 12px;
  overflow: hidden;
  transition: border-color 0.2s;
}
.ticket-card:hover { border-color: var(--accent); }
.ticket-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  cursor: pointer;
  gap: 12px;
  flex-wrap: wrap;
}
.ticket-id { font-weight: 700; font-size: 0.95rem; min-width: 80px; }
.ticket-cat {
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 0.72rem;
  font-weight: 600;
  background: rgba(59,130,246,0.12);
  color: var(--accent);
}
.ticket-conf { font-size: 0.8rem; color: var(--text-dim); }
.res-badge {
  padding: 4px 12px;
  border-radius: 6px;
  font-size: 0.75rem;
  font-weight: 700;
}
.res-resolved { background: rgba(16,185,129,0.15); color: var(--green); }
.res-declined { background: rgba(245,158,11,0.15); color: var(--yellow); }
.res-escalated { background: rgba(59,130,246,0.15); color: var(--accent); }
.res-awaiting { background: rgba(245,158,11,0.15); color: var(--yellow); }
.res-error { background: rgba(239,68,68,0.15); color: var(--red); }

.ticket-detail {
  display: none;
  padding: 0 18px 18px;
  border-top: 1px solid var(--border);
}
.ticket-detail.open { display: block; }

.detail-section { margin-top: 14px; }
.detail-title { font-weight: 600; font-size: 0.85rem; color: var(--cyan); margin-bottom: 6px; }
.step {
  background: var(--bg);
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 6px;
  font-size: 0.82rem;
}
.step-thought { color: var(--yellow); }
.step-action { color: var(--cyan); font-weight: 600; }
.step-obs { color: var(--green); font-size: 0.78rem; }
.step-decision { color: var(--green); font-weight: 600; }

.reply-box {
  background: var(--bg);
  border-left: 3px solid var(--accent);
  padding: 12px 16px;
  border-radius: 0 6px 6px 0;
  font-size: 0.82rem;
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
}
.flag-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.7rem;
  font-weight: 600;
  background: rgba(239,68,68,0.15);
  color: var(--red);
  margin-right: 6px;
}

/* Failure Table */
table { width: 100%; border-collapse: collapse; }
th { text-align: left; padding: 10px 16px; font-size: 0.78rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }
td { padding: 10px 16px; font-size: 0.85rem; border-bottom: 1px solid var(--border); }
tr:hover { background: rgba(59,130,246,0.04); }

/* Eval */
.eval-row {
  display: flex;
  align-items: center;
  padding: 8px 16px;
  border-bottom: 1px solid var(--border);
  font-size: 0.85rem;
  gap: 12px;
}
.eval-icon { font-size: 1.1rem; min-width: 28px; text-align: center; }
.eval-tid { font-weight: 600; min-width: 70px; }
.eval-exp { color: var(--text-dim); flex: 1; font-size: 0.78rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.eval-score { font-weight: 700; min-width: 50px; text-align: right; }

/* Limitations */
.limit-item {
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  font-size: 0.85rem;
  display: flex;
  gap: 10px;
}
.limit-icon { color: var(--yellow); min-width: 20px; }

/* Loading */
.loading {
  text-align: center; padding: 60px;
  color: var(--text-dim); font-size: 1.1rem;
}
.spinner {
  display: inline-block; width: 40px; height: 40px;
  border: 3px solid var(--border); border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-bottom: 16px;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Footer */
.footer { text-align: center; padding: 32px 0; color: var(--text-dim); font-size: 0.8rem; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🛒 ShopWave Autonomous Support Agent</h1>
    <p class="subtitle">Live Dashboard — Processing 20 tickets concurrently with failure recovery</p>
    <div class="badge-row">
      <span class="badge badge-blue">NO LLM</span>
      <span class="badge badge-green">CONCURRENT</span>
      <span class="badge badge-purple">SELF-EVALUATING</span>
    </div>
  </div>

  <div id="content">
    <div class="loading">
      <div class="spinner"></div>
      <br>Running agent on 20 tickets...
    </div>
  </div>
</div>

<script>
async function loadData() {
  const res = await fetch('/api/state');
  const data = await res.json();
  if (data.status !== 'complete') {
    setTimeout(loadData, 500);
    return;
  }
  render(data);
}

function render(data) {
  const s = data.summary;
  const el = document.getElementById('content');

  let html = '';

  // Stats
  html += `<div class="stats-grid">
    <div class="stat-card"><div class="stat-value" style="color:var(--text)">${s.total}</div><div class="stat-label">Tickets</div></div>
    <div class="stat-card"><div class="stat-value" style="color:var(--green)">${s.resolved}</div><div class="stat-label">Resolved</div></div>
    <div class="stat-card"><div class="stat-value" style="color:var(--accent)">${s.escalated}</div><div class="stat-label">Escalated</div></div>
    <div class="stat-card"><div class="stat-value" style="color:var(--yellow)">${s.awaiting}</div><div class="stat-label">Awaiting</div></div>
    <div class="stat-card"><div class="stat-value" style="color:var(--cyan)">${s.autonomous_rate}%</div><div class="stat-label">Autonomous</div></div>
    <div class="stat-card"><div class="stat-value" style="color:var(--purple)">${s.avg_confidence}%</div><div class="stat-label">Confidence</div></div>
    <div class="stat-card"><div class="stat-value" style="color:var(--green)">${s.chain_met}/20</div><div class="stat-label">≥3 Chain</div></div>
    <div class="stat-card"><div class="stat-value" style="color:var(--red)">${s.failures}→${s.recoveries}</div><div class="stat-label">Fail/Recover</div></div>
    <div class="stat-card"><div class="stat-value" style="color:var(--pink)">${data.elapsed}s</div><div class="stat-label">Total Time</div></div>
    <div class="stat-card"><div class="stat-value" style="color:var(--green)">${s.eval_accuracy}%</div><div class="stat-label">Eval Score</div></div>
  </div>`;

  // Tickets
  html += `<div class="section"><div class="section-header">📩 Ticket Resolutions (click to expand)</div><div class="ticket-list">`;
  data.reports.forEach((r, i) => {
    const t = data.tickets[i];
    const resCls = {'resolved':'res-resolved','resolved_declined':'res-declined','escalated':'res-escalated','awaiting_customer_info':'res-awaiting','error':'res-error'}[r.resolution] || '';
    const resLabel = {'resolved':'✅ Resolved','resolved_declined':'⛔ Declined','escalated':'⬆️ Escalated','awaiting_customer_info':'⏳ Awaiting','error':'❌ Error'}[r.resolution] || r.resolution;
    const flags = (r.flags||[]).map(f=>`<span class="flag-tag">${f}</span>`).join('');

    // Find reply from audit log
    let reply = '';
    for (let j = data.audit_log.length-1; j >= 0; j--) {
      if (data.audit_log[j].ticket_id === r.ticket_id && data.audit_log[j].tool === 'send_reply') {
        reply = data.audit_log[j].output.message_sent || '';
        break;
      }
    }

    html += `<div class="ticket-card">
      <div class="ticket-top" onclick="this.nextElementSibling.classList.toggle('open')">
        <span class="ticket-id">${r.ticket_id}</span>
        <span class="ticket-cat">${r.category}</span>
        ${flags}
        <span class="ticket-conf">🎯 ${Math.round(r.confidence_score*100)}%</span>
        <span class="ticket-conf">🔧 ${r.tool_call_count} tools</span>
        <span class="res-badge ${resCls}">${resLabel}</span>
      </div>
      <div class="ticket-detail">
        <div class="detail-section">
          <div class="detail-title">📧 Ticket</div>
          <div class="step"><strong>${t.subject}</strong><br><span style="color:var(--text-dim)">${t.customer_email} · ${t.source} · ${t.created_at}</span><br>${t.body.substring(0,300)}</div>
        </div>
        <div class="detail-section">
          <div class="detail-title">🧠 Reasoning Chain (${r.total_steps} steps)</div>
          ${(r.reasoning_steps||[]).map((s,j)=>{
            let obs = s.observation || '';
            let obsCls = obs.startsWith('DECISION:') ? 'step-decision' : 'step-obs';
            return `<div class="step">
              <span style="color:var(--text-dim)">[${j+1}]</span>
              <span class="step-thought"> 💭 ${s.thought ? s.thought.substring(0,150) : ''}</span>
              ${s.action ? `<br><span class="step-action">🔧 ${s.action}</span>` : ''}
              ${obs ? `<br><span class="${obsCls}">👁 ${obs.substring(0,150)}</span>` : ''}
            </div>`;
          }).join('')}
        </div>
        ${reply ? `<div class="detail-section">
          <div class="detail-title">📨 Reply Sent</div>
          <div class="reply-box">${reply.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</div>
        </div>` : ''}
      </div>
    </div>`;
  });
  html += '</div></div>';

  // Failure Recovery
  const failures = data.audit_log.filter(e => e.tool && e.tool.includes('failure'));
  const recoveries = data.audit_log.filter(e => e.recovered_from_failure);
  if (failures.length > 0) {
    html += `<div class="section"><div class="section-header">🔄 Failure Recovery Log</div>
    <table><tr><th>Ticket</th><th>Tool</th><th>Failure Type</th><th>Error</th><th>Recovery</th></tr>`;
    failures.forEach(f => {
      const rec = recoveries.find(r => r.ticket_id === f.ticket_id && r.tool.replace('::recovery','') === f.tool.replace('::failure',''));
      html += `<tr>
        <td>${f.ticket_id}</td>
        <td style="color:var(--cyan)">${f.tool.replace('::failure','')}</td>
        <td><span class="flag-tag">${f.output.error_type || 'Unknown'}</span></td>
        <td style="font-size:0.78rem;color:var(--text-dim)">${(f.output.error||'').substring(0,80)}</td>
        <td>${rec ? '<span style="color:var(--green)">✅ Attempt #'+rec.attempt+'</span>' : '<span style="color:var(--red)">❌</span>'}</td>
      </tr>`;
    });
    html += '</table></div>';
  }

  // Self-Evaluation
  if (data.evaluation && data.evaluation.details) {
    const ev = data.evaluation;
    html += `<div class="section"><div class="section-header">🧪 Self-Evaluation (Agent vs Expected Actions) — Score: ${Math.round(ev.weighted_score*100)}%</div>`;
    ev.details.forEach(d => {
      const icon = {'CORRECT':'✅','PARTIAL':'🔶','INCORRECT':'❌','UNKNOWN':'❓'}[d.match] || '❓';
      const color = {'CORRECT':'var(--green)','PARTIAL':'var(--yellow)','INCORRECT':'var(--red)','UNKNOWN':'var(--text-dim)'}[d.match];
      html += `<div class="eval-row">
        <span class="eval-icon">${icon}</span>
        <span class="eval-tid">${d.ticket_id}</span>
        <span class="eval-exp" title="${d.expected_action}">${d.expected_action.substring(0,80)}</span>
        <span style="font-size:0.75rem;color:var(--text-dim)">${d.explanation.substring(0,50)}</span>
        <span class="eval-score" style="color:${color}">${Math.round(d.score*100)}%</span>
      </div>`;
    });
    html += '</div>';

    // Known Limitations (self-awareness)
    const sa = ev.self_assessment;
    if (sa && sa.known_limitations) {
      html += `<div class="section"><div class="section-header">🤖 Agent Self-Assessment & Known Limitations</div>`;
      html += `<div class="limit-item"><span class="limit-icon">📊</span> <strong>Accuracy:</strong>&nbsp;${sa.overall_accuracy}</div>`;
      html += `<div class="limit-item"><span class="limit-icon">🎯</span> <strong>Calibration:</strong>&nbsp;${sa.confidence_calibration}</div>`;
      sa.known_limitations.forEach(l => {
        html += `<div class="limit-item"><span class="limit-icon">⚠️</span> ${l}</div>`;
      });
      html += '</div>';
    }
  }

  html += `<div class="footer">ShopWave Autonomous Support Agent v3 · Ksolves Agentic AI Hackathon 2026 · No LLM · Pure Python</div>`;
  el.innerHTML = html;
}

loadData();
</script>
</body>
</html>"""


class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/' or path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode('utf-8'))
        elif path == '/api/state':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(DASHBOARD_STATE, default=str).encode('utf-8'))
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass  # Suppress request logging


def main():
    PORT = 8080

    print(f"""
\033[96m\033[1m╔══════════════════════════════════════════════════════════════╗
║     🛒 ShopWave Autonomous Support Agent — Live Dashboard    ║
╚══════════════════════════════════════════════════════════════╝\033[0m
""")
    print(f"  \033[1mStarting agent...\033[0m")

    # Run agent in background thread
    agent_thread = threading.Thread(target=run_agent, daemon=True)
    agent_thread.start()

    # Start web server
    server = HTTPServer(('', PORT), DashboardHandler)
    print(f"  \033[92m✅ Dashboard live at:\033[0m \033[1mhttp://localhost:{PORT}\033[0m")
    print(f"  \033[2mPress Ctrl+C to stop.\033[0m\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n  \033[93mShutting down...\033[0m")
        server.server_close()


if __name__ == "__main__":
    main()
