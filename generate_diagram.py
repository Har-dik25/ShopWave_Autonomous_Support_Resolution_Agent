import matplotlib.pyplot as plt
import matplotlib.patches as patches

fig, ax = plt.subplots(figsize=(12, 8))
ax.set_xlim(0, 10)
ax.set_ylim(0, 10)
ax.axis('off')

# Colors
bg_color = "#f8f9fa"
box_color = "#eaf2fb"
edge_color = "#3b82f6"
text_color = "#1e293b"
fig.patch.set_facecolor(bg_color)

def draw_box(x, y, w, h, text):
    rect = patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.2",
                                  linewidth=2, edgecolor=edge_color, facecolor=box_color)
    ax.add_patch(rect)
    ax.text(x + w/2, y + h/2, text, ha='center', va='center',
            fontsize=12, fontweight='bold', color=text_color, wrap=True)

def draw_arrow(x1, y1, x2, y2, text=""):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="->", color=edge_color, lw=2))
    if text:
        ax.text((x1+x2)/2, (y1+y2)/2 + 0.2, text, ha='center', fontsize=10, color="#64748b")

# Draw components
draw_box(4, 8, 2, 1, "Tickets Queue\n(data/tickets.json)")
draw_box(4, 5.5, 2, 1.2, "Classifier\n(Rule-based)")
draw_box(1, 2.5, 2.5, 1.5, "Data Tools\n(get_order, get_customer)")
draw_box(4.25, 2.5, 1.5, 1.5, "Agent Hub\n(Resolver)")
draw_box(6.5, 2.5, 2.5, 1.5, "Action Tools\n(issue_refund, reply)")
draw_box(4, 0, 2, 1, "Audit Log\n(JSON output)")

# Connectors
draw_arrow(5, 8, 5, 6.7, "Fetch")
draw_arrow(5, 5.5, 5, 4, "Categorize & Route")

draw_arrow(4.25, 3.25, 3.5, 3.25, "Lookup")
draw_arrow(3.5, 3.0, 4.25, 3.0, "State")

draw_arrow(5.75, 3.25, 6.5, 3.25, "Execute")
draw_arrow(6.5, 3.0, 5.75, 3.0, "Result")

draw_arrow(5, 2.5, 5, 1, "Log Trace")

plt.title("ShopWave Autonomous Agent Architecture", fontsize=18, fontweight='bold', color=text_color, pad=20)
plt.tight_layout()
plt.savefig("architecture.png", dpi=300, bbox_inches='tight')
print("Architecture PNG generated!")
