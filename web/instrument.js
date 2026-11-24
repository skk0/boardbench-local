/* Offline illustration of the active link. No external assets or simulated telemetry. */
function drawInstrument(connected, transport) {
  const canvas = document.getElementById("boardArt");
  const g = canvas.getContext("2d");
  const unit = canvas.width / 340;
  g.setTransform(unit, 0, 0, unit, 0, 0);
  g.clearRect(0, 0, 340, 140);
  const point = (x, y, z = 0) => [
    111 + x * 0.97 - y * 0.58,
    44 - x * 0.16 + y * 0.66 - z,
  ];
  function path(pts, fill, outline) {
    g.beginPath();
    pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
    if (fill) {
      g.closePath();
      g.fillStyle = fill;
      g.fill();
    }
    if (outline) {
      g.strokeStyle = outline;
      g.lineWidth = 0.65;
      g.stroke();
    }
  }
  function body(x, y, w, h, z, thick, top, front, edge) {
    const a = point(x, y, z),
      b = point(x + w, y, z),
      c = point(x + w, y + h, z),
      d = point(x, y + h, z);
    path(
      [d, c, point(x + w, y + h, z - thick), point(x, y + h, z - thick)],
      front,
      edge,
    );
    path(
      [b, c, point(x + w, y + h, z - thick), point(x + w, y, z - thick)],
      front,
      edge,
    );
    path([a, b, c, d], top, edge);
  }
  body(0, 0, 202, 108, 0, 5, "#264d53", "#10272a", "#78a59e");
  // Parallel gold traces and vias on the edge of the board.
  for (let i = 0; i < 8; i++) {
    path(
      [
        [16, 17 + i * 5],
        [30 + i * 2, 17 + i * 5],
        [37 + i * 2, 24 + i * 5],
        [45 + i * 2, 24 + i * 5],
      ].map((p) => point(...p, 1)),
      null,
      "#669992",
    );
  }
  for (const x of [7, 194])
    for (const y of [8, 99]) {
      const [cx, cy] = point(x, y, 1);
      g.fillStyle = "#baaa72";
      g.beginPath();
      g.ellipse(cx, cy, 3.8, 2.5, -0.2, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#122b2e";
      g.beginPath();
      g.ellipse(cx, cy, 1.8, 1.1, -0.2, 0, Math.PI * 2);
      g.fill();
    }
  for (let x = 20; x < 183; x += 8)
    for (const y of [4, 99])
      body(x, y, 4.2, 5, 1, 0.6, "#d4ac66", "#816940", null);
  body(42, 17, 106, 68, 6, 6, "#202b31", "#0d1519", "#9ea6a2");
  body(
    48,
    23,
    94,
    54,
    6.5,
    0.5,
    connected ? "#263e42" : "#1a2e35",
    "#101f25",
    "#385258",
  );
  // Subtle glass reflection on the display.
  path(
    [
      [53, 24],
      [84, 24],
      [118, 76],
      [88, 76],
    ].map((p) => point(...p, 7)),
    "#ffffff09",
    null,
  );
  const [sx, sy] = point(66, 46, 7);
  g.save();
  g.transform(0.97, -0.16, -0.58, 0.66, sx, sy);
  g.fillStyle = connected ? "#78e6b8" : "#8eaaa9";
  g.font = "bold 8px monospace";
  g.fillText(connected ? transport + " LINK" : "STANDBY", 0, 0);
  g.restore();
  body(157, 24, 26, 40, 4, 4, "#182226", "#10191c", "#748685");
  for (let i = 0; i < 8; i++)
    for (const xx of [153, 184])
      body(xx, 26 + i * 4.5, 4, 1.7, 1, 0.5, "#ccd2c7", "#79817b", null);
  for (let i = 0; i < 3; i++) {
    body(44 + i * 27, 86, 16, 8, 4, 3, "#b4bdb7", "#555f60", "#b0bdb6");
    body(48 + i * 27, 87, 8, 5, 6, 2, "#333d3c", "#1d2727", null);
  }
  for (let i = 0; i < 8; i++)
    body(
      154 + (i % 4) * 8,
      73 + Math.floor(i / 4) * 7,
      4,
      2.8,
      2,
      1,
      "#c9ad80",
      "#6b6358",
      null,
    );
  body(-6, 57, 22, 20, 7, 7, "#c0c9c5", "#727f81", "#d0d5cb");
  const [lx, ly] = point(23, 80, 2);
  g.fillStyle = connected ? "#72e4a8" : "#f1a653";
  g.beginPath();
  g.arc(lx, ly, 2.1, 0, Math.PI * 2);
  g.fill();
}
drawInstrument(false, "USB");
