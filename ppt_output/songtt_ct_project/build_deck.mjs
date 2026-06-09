import pptxgen from "pptxgenjs";
import fs from "node:fs";
import path from "node:path";

const ROOT = "/Users/thy/Desktop/KeTiZu/proj_260608/Songtt_proj01";
const OUT = path.join(ROOT, "ppt_output/songtt_ct_project/output_integrated_verbose_with_home_report.pptx");
const P1 = path.join(ROOT, "output_nifti/patient_0099039449");
const P2 = path.join(ROOT, "output_nifti/patient_0100297683");
const REPORT_PAGES_DIR = path.join(ROOT, "ppt_output/songtt_ct_project/report_pages");

const img = {
  p1Preview: path.join(P1, "CT_brain_0.80_Hr40_S3_00000262_preview.png"),
  p2Preview: path.join(P2, "CT_brain_0.80_Hr40_S3_00000269_preview.png"),
  skullOverlay: path.join(P1, "CT_brain_0.80_Hr60_S3_00000263_skull_overlay.png"),
  skull3d: path.join(P1, "CT_brain_0.80_Hr60_S3_00000263_skull_3d.png"),
  brainOverlay: path.join(P1, "CT_brain_0.80_Hr40_S3_00000262_brain_overlay.png"),
  brain3d: path.join(P1, "CT_brain_0.80_Hr40_S3_00000262_brain_3d.png"),
  ventOverlay: path.join(P1, "CT_brain_0.80_Hr40_S3_00000262_ventricle_overlay.png"),
  vent3d: path.join(P1, "CT_brain_0.80_Hr40_S3_00000262_ventricle_3d.png"),
  vesselOverlay: path.join(P1, "CT_brain_0.80_Hr40_S3_00000262_vessel_overlay.png"),
  vessel3d: path.join(P1, "CT_brain_0.80_Hr40_S3_00000262_vessel_3d.png"),
  brainstemOverlay: path.join(P1, "CT_brain_0.80_Hr40_S3_00000262_brainstem_overlay.png"),
  eloquentOverlay: path.join(P1, "CT_brain_0.80_Hr40_S3_00000262_eloquent_overlay.png"),
  pathsOverlay: path.join(P1, "CT_brain_0.80_Hr40_S3_00000262_paths_overlay.png"),
  paths3d: path.join(P1, "CT_brain_0.80_Hr40_S3_00000262_paths_3d.png"),
};

const reportPageImages = fs.existsSync(REPORT_PAGES_DIR)
  ? fs.readdirSync(REPORT_PAGES_DIR)
      .filter(name => /^report_page_\d+\.png$/.test(name))
      .sort()
      .map(name => path.join(REPORT_PAGES_DIR, name))
  : [];

const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "Songtt CT project";
pptx.company = "KeTiZu";
pptx.subject = "Brain CT segmentation and puncture path planning pipeline";
pptx.title = "脑出血 CT 影像分割与穿刺路径规划";
pptx.lang = "zh-CN";
pptx.theme = {
  headFontFace: "Heiti TC",
  bodyFontFace: "Heiti TC",
  lang: "zh-CN",
};
pptx.defineLayout({ name: "LAYOUT_WIDE", width: 13.333, height: 7.5 });

const C = {
  bg: "07111F",
  panel: "0E1A2B",
  panel2: "13243A",
  ink: "EAF2FF",
  muted: "9DB0C8",
  sub: "C9D7EA",
  cyan: "38BDF8",
  red: "F05252",
  orange: "F59E0B",
  purple: "A78BFA",
  green: "34D399",
  line: "29445F",
  white: "FFFFFF",
};
const FONT = "Heiti TC";

function addBg(slide, section = "CT PIPELINE") {
  slide.background = { color: C.bg };
  slide.addShape(pptx.ShapeType.rect, {
    x: 0, y: 0, w: 13.333, h: 7.5,
    fill: { color: C.bg },
    line: { color: C.bg, transparency: 100 },
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: 0, y: 0, w: 13.333, h: 0.13,
    fill: { color: C.cyan },
    line: { color: C.cyan, transparency: 100 },
  });
  slide.addText(section, {
    x: 0.55, y: 0.28, w: 2.2, h: 0.24,
    margin: 0, fontFace: FONT, fontSize: 7.5,
    color: C.cyan, bold: true, charSpace: 1.2,
  });
}

function title(slide, txt, sub = "") {
  slide.addText(txt, {
    x: 0.55, y: 0.58, w: 7.8, h: 0.44,
    margin: 0, fontFace: FONT, fontSize: 23,
    color: C.ink, bold: true, breakLine: false,
  });
  if (sub) {
    slide.addText(sub, {
      x: 0.57, y: 1.08, w: 9.2, h: 0.28,
      margin: 0, fontFace: FONT, fontSize: 10.2,
      color: C.muted,
    });
  }
}

function footer(slide, idx) {
  slide.addText(`Songtt_proj01 · 自动化流水线汇报 · ${idx}`, {
    x: 0.55, y: 7.15, w: 3.7, h: 0.18,
    margin: 0, fontFace: FONT, fontSize: 6.5, color: "6F819B",
  });
}

function card(slide, x, y, w, h, opts = {}) {
  slide.addShape(pptx.ShapeType.roundRect, {
    x, y, w, h,
    rectRadius: 0.06,
    fill: { color: opts.fill || C.panel, transparency: opts.transparency ?? 0 },
    line: { color: opts.line || C.line, transparency: opts.lineTransparency ?? 10, width: opts.lineWidth || 1 },
  });
}

function kpi(slide, x, y, w, label, value, accent = C.cyan, note = "") {
  card(slide, x, y, w, 0.83, { fill: C.panel2, line: accent, lineTransparency: 35 });
  slide.addText(value, {
    x: x + 0.18, y: y + 0.13, w: w - 0.36, h: 0.27,
    margin: 0, fontFace: FONT, fontSize: 18, color: accent, bold: true,
  });
  slide.addText(label, {
    x: x + 0.18, y: y + 0.46, w: w - 0.36, h: 0.18,
    margin: 0, fontFace: FONT, fontSize: 7.8, color: C.sub, bold: true,
  });
  if (note) {
    slide.addText(note, {
      x: x + 0.18, y: y + 0.65, w: w - 0.36, h: 0.12,
      margin: 0, fontFace: FONT, fontSize: 5.9, color: C.muted,
    });
  }
}

function bulletBlock(slide, x, y, w, h, heading, bullets, accent = C.cyan) {
  card(slide, x, y, w, h);
  slide.addText(heading, {
    x: x + 0.25, y: y + 0.22, w: w - 0.5, h: 0.24,
    margin: 0, fontFace: FONT, fontSize: 11.2, color: accent, bold: true,
  });
  slide.addText(bullets.map(b => `• ${b}`).join("\n"), {
    x: x + 0.25, y: y + 0.6, w: w - 0.48, h: h - 0.75,
    margin: 0, fontFace: FONT, fontSize: 8.8,
    color: C.sub, breakLine: false, fit: "shrink",
    valign: "top",
    paraSpaceAfterPt: 4,
  });
}

function paragraphBlock(slide, x, y, w, h, heading, body, accent = C.cyan) {
  card(slide, x, y, w, h);
  slide.addText(heading, {
    x: x + 0.22, y: y + 0.2, w: w - 0.44, h: 0.22,
    margin: 0, fontFace: FONT, fontSize: 10.8, color: accent, bold: true,
    fit: "shrink",
  });
  slide.addText(body, {
    x: x + 0.22, y: y + 0.55, w: w - 0.44, h: h - 0.7,
    margin: 0, fontFace: FONT, fontSize: 8.5,
    color: C.sub, breakLine: false, fit: "shrink",
    valign: "top", paraSpaceAfterPt: 4,
  });
}

function explainNote(slide, x, y, w, h, heading, body, accent = C.cyan) {
  const compact = h < 0.7;
  card(slide, x, y, w, h, { fill: "09182A", line: accent, lineTransparency: 35 });
  slide.addText(heading, {
    x: x + 0.18, y: y + (compact ? 0.08 : 0.14), w: w - 0.36, h: compact ? 0.13 : 0.16,
    margin: 0, fontFace: FONT, fontSize: compact ? 7.6 : 8.5, color: accent, bold: true,
    fit: "shrink",
  });
  slide.addText(body, {
    x: x + 0.18, y: y + (compact ? 0.25 : 0.38), w: w - 0.36, h: Math.max(0.12, h - (compact ? 0.31 : 0.5)),
    margin: 0, fontFace: FONT, fontSize: compact ? 6.4 : 7.2, color: C.sub,
    breakLine: false, fit: "shrink", valign: "top",
    paraSpaceAfterPt: 2,
  });
}

function imageFrame(slide, p, x, y, w, h, caption = "", mode = "contain") {
  card(slide, x, y, w, h, { fill: "081421", line: "203A55" });
  if (fs.existsSync(p)) {
    slide.addImage({
      path: p,
      x: x + 0.06, y: y + 0.06, w: w - 0.12, h: h - (caption ? 0.42 : 0.12),
      sizing: { type: mode, x: x + 0.06, y: y + 0.06, w: w - 0.12, h: h - (caption ? 0.42 : 0.12) },
    });
  } else {
    slide.addText("缺少图片", { x, y: y + h / 2 - 0.1, w, h: 0.2, align: "center", color: C.muted, fontFace: FONT, fontSize: 9 });
  }
  if (caption) {
    slide.addText(caption, {
      x: x + 0.16, y: y + h - 0.3, w: w - 0.32, h: 0.17,
      margin: 0, fontFace: FONT, fontSize: 7.3, color: C.muted,
      align: "center",
    });
  }
}

function statTable(slide, x, y, cols, rows, widths, opts = {}) {
  const rowH = opts.rowH || 0.34;
  const headH = opts.headH || 0.37;
  let curX = x;
  cols.forEach((c, i) => {
    slide.addShape(pptx.ShapeType.rect, { x: curX, y, w: widths[i], h: headH, fill: { color: opts.headFill || "18304A" }, line: { color: C.line, width: 0.5 } });
    slide.addText(c, { x: curX + 0.05, y: y + 0.09, w: widths[i] - 0.1, h: 0.15, margin: 0, fontFace: FONT, fontSize: 7.3, bold: true, color: C.ink, align: opts.align?.[i] || "left" });
    curX += widths[i];
  });
  rows.forEach((r, ri) => {
    curX = x;
    r.forEach((c, i) => {
      slide.addShape(pptx.ShapeType.rect, { x: curX, y: y + headH + ri * rowH, w: widths[i], h: rowH, fill: { color: ri % 2 === 0 ? "0D1B2C" : "102238" }, line: { color: C.line, width: 0.45 } });
      slide.addText(String(c), { x: curX + 0.05, y: y + headH + ri * rowH + 0.09, w: widths[i] - 0.1, h: 0.14, margin: 0, fontFace: FONT, fontSize: 7.2, color: i === 0 ? C.sub : C.ink, align: opts.align?.[i] || "left" });
      curX += widths[i];
    });
  });
}

function stepChip(slide, x, y, n, label, color = C.cyan) {
  slide.addShape(pptx.ShapeType.ellipse, { x, y, w: 0.38, h: 0.38, fill: { color }, line: { color, transparency: 100 } });
  slide.addText(String(n), { x, y: y + 0.09, w: 0.38, h: 0.12, margin: 0, fontFace: FONT, fontSize: 7.5, color: C.bg, bold: true, align: "center" });
  slide.addText(label, { x: x + 0.48, y: y + 0.06, w: 2.0, h: 0.18, margin: 0, fontFace: FONT, fontSize: 8.3, color: C.sub, bold: true });
}

function addSlideBase(idx, t, sub, section) {
  const slide = pptx.addSlide();
  addBg(slide, section);
  title(slide, t, sub);
  footer(slide, idx);
  return slide;
}

function speaker(slide, lines) {
  slide.addNotes(lines.join("\n\n"));
}

// 1. Home page
{
  const s = pptx.addSlide();
  addBg(s, "PRESENTATION");
  s.addShape(pptx.ShapeType.rect, { x: 7.35, y: 0.13, w: 5.98, h: 7.37, fill: { color: "020916" }, line: { color: "020916", transparency: 100 } });
  imageFrame(s, img.p1Preview, 7.75, 1.12, 5.0, 3.15, "代表病例 CT 三视图预览", "contain");
  imageFrame(s, img.paths3d, 7.75, 4.55, 5.0, 1.72, "路径规划 3D 结果", "contain");
  s.addText("脑出血 CT 影像分割与\n穿刺路径规划流水线", {
    x: 0.68, y: 1.15, w: 6.2, h: 0.95,
    margin: 0, fontFace: FONT, fontSize: 27, color: C.ink, bold: true,
    breakLine: false, fit: "shrink",
  });
  s.addText("项目汇报", {
    x: 0.72, y: 2.35, w: 2.2, h: 0.25,
    margin: 0, fontFace: FONT, fontSize: 12, color: C.cyan, bold: true,
  });
  card(s, 0.72, 3.18, 5.9, 2.12, { fill: C.panel2, line: C.cyan, lineTransparency: 20 });
  s.addText("汇报人信息", {
    x: 1.0, y: 3.48, w: 2.2, h: 0.22,
    margin: 0, fontFace: FONT, fontSize: 12, color: C.cyan, bold: true,
  });
  s.addText("姓名：____________________\n分组：____________________", {
    x: 1.0, y: 3.98, w: 4.8, h: 0.72,
    margin: 0, fontFace: FONT, fontSize: 15, color: C.ink,
    breakLine: false, fit: "shrink",
  });
  explainNote(s, 0.72, 5.72, 5.9, 0.78, "汇报主题",
    "本汇报展示从原始 DICOM CT 数据开始，到三维影像转换、关键结构分割、风险禁区构建、穿刺路径规划和报告输出的完整处理流程。", C.green);
  footer(s, 1);
  speaker(s, [
    "这一页是汇报首页，用来填写姓名和分组。打开 PowerPoint 后可以直接修改姓名和分组两行文字。",
    "汇报主题是脑出血 CT 影像分割与穿刺路径规划流水线，后面的页面会按照数据转换、结构分割、禁区构建、路径规划和报告输出的顺序展开。"
  ]);
}

// 2. Project cover
{
  const s = pptx.addSlide();
  addBg(s, "PROJECT REPORT");
  s.addShape(pptx.ShapeType.rect, { x: 7.45, y: 0.13, w: 5.88, h: 7.37, fill: { color: "020916" }, line: { color: "020916", transparency: 100 } });
  imageFrame(s, img.paths3d, 7.75, 0.75, 5.12, 5.95, "代表病例路径规划 3D 视图", "contain");
  s.addText("脑出血 CT 影像分割与\n穿刺路径规划流水线", {
    x: 0.65, y: 1.12, w: 6.25, h: 0.9,
    margin: 0, fontFace: FONT, fontSize: 27, color: C.ink, bold: true,
    breakLine: false, fit: "shrink",
  });
  s.addText("从原始 DICOM 到关键结构分割、风险禁区生成、路径规划与报告输出", {
    x: 0.68, y: 2.28, w: 5.8, h: 0.3,
    margin: 0, fontFace: FONT, fontSize: 11, color: C.muted,
  });
  kpi(s, 0.7, 3.22, 1.35, "病例数", "2", C.cyan);
  kpi(s, 2.2, 3.22, 1.35, "转换序列", "8", C.green);
  kpi(s, 3.7, 3.22, 1.65, "疑似血肿", "8.37 mL", C.red, "1 个病例检出");
  kpi(s, 5.55, 3.22, 1.35, "合法路径", "2", C.orange);
  bulletBlock(s, 0.7, 4.5, 6.15, 1.7, "汇报重点", [
    "完整跑通从原始 CT 到路径规划的 9 步处理流程",
    "每一步都留下可检查产物：三维影像、mask、叠加图、3D 图、JSON 和 PDF",
    "当前是规则/几何驱动的辅助原型，价值在于自动整理和可视化，不是直接给临床结论",
  ], C.cyan);
  explainNote(s, 0.7, 6.25, 6.15, 0.68, "项目定位",
    "可以把它理解成“影像整理 + 自动标注 + 路径初筛”工具：先把 CT 拼成三维脑袋，再把骨头、脑组织、疑似血肿和危险区域涂出来，最后给出几条候选直线路径，方便人去检查。", C.red);
  footer(s, 2);
  speaker(s, [
    "这一页先讲项目的定位。不要把它说成自动诊断或者自动手术决策系统，它更像一个影像辅助原型。",
    "项目做的事情可以用一句话概括：先把医院导出的 CT 小切片整理成三维脑部 CT，再把骨头、脑组织、疑似血肿、脑室和一些风险区域标出来，最后用这些标注结果筛选候选穿刺路径。",
    "右侧 3D 图展示的是最终路径规划结果。左侧四个数字说明这次数据里有 2 个病例、8 个成功转换的体数据序列、1 个病例检出 8.37 mL 疑似血肿、最终筛出 2 条合法路径。"
  ]);
}

// 2. Objective
{
  const s = addSlideBase(3, "项目目标与最终交付", "把 CT 原始数据转化为可检查、可汇报、可追踪的结构化结果", "OBJECTIVE");
  const cards = [
    ["输入", "医院导出的 DICOM 切片\n包含图像、空间位置和扫描参数", C.cyan],
    ["分割", "标出颅骨、颅腔、脑组织、血肿\n以及脑室等结构", C.green],
    ["规划", "在避开风险禁区的前提下\n寻找候选穿刺直线路径", C.orange],
    ["报告", "输出 PNG、JSON、TXT 和 PDF\n便于复核和汇报", C.purple],
  ];
  cards.forEach((c, i) => {
    const x = 0.75 + i * 3.05;
    card(s, x, 1.65, 2.65, 2.55, { fill: C.panel2, line: c[2] });
    s.addText(c[0], { x: x + 0.22, y: 1.9, w: 2.2, h: 0.25, margin: 0, fontFace: FONT, fontSize: 16, bold: true, color: c[2] });
    s.addText(c[1], { x: x + 0.22, y: 2.55, w: 2.22, h: 0.75, margin: 0, fontFace: FONT, fontSize: 9.2, color: C.sub, breakLine: false, fit: "shrink" });
  });
  bulletBlock(s, 0.75, 4.7, 5.85, 1.58, "当前项目定位", [
    "它不是自动诊断系统，也不能替代医生判断；PPT 里的“血肿”“禁区”“路径”都应理解为程序计算出的候选结果",
    "核心价值是把分散的 CT 文件、分割结果、统计数据和路径图统一整理，让人能快速复核每一步",
    "适合做原型验证和流程演示；后续可以替换成更准确的血管分割、功能区定位或深度学习模型",
  ], C.red);
  bulletBlock(s, 6.95, 4.7, 5.6, 1.58, "本次已跑出的结果", [
    "2 个病例完成 DICOM 转换、颅骨、脑组织、脑室、血管风险、脑干和功能区禁区生成",
    "病例 0099039449 检出疑似血肿 8.37 mL，并找到 2 条满足规则的候选路径",
    "病例 0100297683 没有检出血肿，因此路径规划自动跳过；两个病例均生成了文字方案和 PDF 报告",
  ], C.green);
  explainNote(s, 0.75, 6.42, 11.8, 0.48, "给听众的理解方式",
    "整套流程的目标不是“程序说了算”，而是把原本需要人工逐项打开查看的文件变成一组清晰、可追踪、可复核的中间结果。", C.cyan);
  speaker(s, [
    "这一页说明最终交付。输入是医院 CT 机器导出的 DICOM 文件，它们不是普通照片，而是带空间坐标和扫描参数的医学影像文件。",
    "分割部分的输出是各种 mask，可以理解为给 CT 加了很多透明涂色层：骨头一层、脑组织一层、血肿一层、脑室一层、风险禁区一层。",
    "规划部分不是直接给临床方案，而是在这些涂色层的限制下，先自动找出一些候选直线路径。报告部分则把 PNG、JSON、TXT、PDF 都整理出来，方便后续人工复核。"
  ]);
}

// 3. Data overview
{
  const s = addSlideBase(4, "数据与运行结果概览", "本次基于已解压的头颅 CT 数据完成端到端跑通", "DATA");
  statTable(s, 0.7, 1.55,
    ["项目", "结果"],
    [
      ["遍历文件", "1003 个"],
      ["识别 DICOM", "1001 个"],
      ["DICOM series", "11 个"],
      ["成功转换", "8 个体数据序列"],
      ["跳过序列", "3 个（定位像/患者方案等）"],
      ["失败序列", "0"],
    ],
    [1.55, 3.0],
    { align: ["left", "left"], rowH: 0.36 });
  statTable(s, 0.7, 4.45,
    ["病例", "颅腔", "脑组织", "血肿"],
    [
      ["0099039449", "827.24 mL", "785.40 mL", "8.37 mL"],
      ["0100297683", "1048.32 mL", "999.50 mL", "0.00 mL"],
    ],
    [1.4, 1.25, 1.25, 1.15],
    { align: ["left", "right", "right", "right"], rowH: 0.38 });
  explainNote(s, 0.7, 5.74, 4.55, 0.82, "怎么读这页",
    "1001 个 DICOM 文件不是 1001 个病例，而是许多张 CT 切片。程序先识别哪些切片属于同一次扫描，再把它们按空间顺序叠成三维体数据；定位像、患者方案这类不能组成可靠三维 CT 的内容会被跳过。", C.cyan);
  imageFrame(s, img.p1Preview, 5.7, 1.52, 6.9, 2.35, "病例 0099039449 · 薄层软组织 CT 三视图", "contain");
  imageFrame(s, img.p2Preview, 5.7, 4.15, 6.9, 2.35, "病例 0100297683 · 薄层软组织 CT 三视图", "contain");
  speaker(s, [
    "这里要解释一个容易误会的点：1001 个 DICOM 不是 1001 个病人，而是一张张切片文件。",
    "程序扫描后发现 11 个 series，其中有些只是定位像或者患者方案，不能当成完整三维 CT 使用，所以被跳过。最终 8 个序列成功转换。",
    "右边两张 preview 是转换后的三视图。简单说，就是从横断面、冠状面和矢状面三个方向快速看三维 CT 是否拼接正常。"
  ]);
}

// 4. Pipeline
{
  const s = addSlideBase(5, "全流程总览", "9 个步骤串起数据读取、分割、风险建模、路径规划和报告输出", "PIPELINE");
  const steps = [
    ["DICOM 转 NIfTI", C.cyan],
    ["颅骨分割", C.green],
    ["脑/血肿分割", C.red],
    ["脑室分割", C.cyan],
    ["血管风险禁区", C.orange],
    ["脑干近似", C.orange],
    ["功能区近似", C.purple],
    ["路径规划", C.green],
    ["报告生成", C.cyan],
  ];
  steps.forEach((st, i) => {
    const row = i < 5 ? 0 : 1;
    const col = row === 0 ? i : i - 5;
    const x = 0.78 + col * 2.45 + (row ? 1.2 : 0);
    const y = row ? 4.35 : 2.0;
    card(s, x, y, 1.95, 1.0, { fill: C.panel2, line: st[1] });
    s.addText(String(i + 1).padStart(2, "0"), { x: x + 0.16, y: y + 0.18, w: 0.45, h: 0.18, margin: 0, fontFace: FONT, fontSize: 8.5, color: st[1], bold: true });
    s.addText(st[0], { x: x + 0.15, y: y + 0.5, w: 1.65, h: 0.2, margin: 0, fontFace: FONT, fontSize: 8.3, color: C.ink, bold: true, align: "center", fit: "shrink" });
    if (i !== 4 && i !== 8) {
      s.addShape(pptx.ShapeType.rightArrow, { x: x + 1.95, y: y + 0.36, w: 0.34, h: 0.28, fill: { color: "34516E" }, line: { color: "34516E", transparency: 100 } });
    }
  });
  bulletBlock(s, 0.8, 5.72, 11.75, 1.04, "汇报时的一句话解释", [
    "这条流水线先把 CT 小切片拼成三维脑部模型；再根据 CT 亮度值、形态学清理和解剖位置，逐步生成骨头、脑组织、血肿和禁区 mask；最后把这些 mask 当作“地图上的障碍物”，从颅骨外表面尝试连线到血肿，筛掉穿过危险区域的路径。",
  ], C.cyan);
  speaker(s, [
    "这一页讲整体流程。可以把整个项目比作一条工厂流水线，每一步拿上一环节的结果继续加工。",
    "第 1 步统一数据格式，第 2 到第 4 步找出骨头、脑组织、血肿和脑室，第 5 到第 7 步画出血管风险、脑干和功能区这些避让区域，第 8 步做路径规划，第 9 步生成报告。",
    "重点强调：后续路径规划不是凭空找线，而是依赖前面每一步产生的 mask。前面 mask 如果错了，后面的路径也会受影响，所以每一步的 overlay 图都要人工看。"
  ]);
}

// 5. DICOM
{
  const s = addSlideBase(6, "步骤 1 · DICOM 转 NIfTI", "目的：把医院导出的原始 CT 切片整理成后续算法可处理的三维体数据", "STEP 1");
  bulletBlock(s, 0.68, 1.55, 4.35, 2.0, "做了什么", [
    "递归扫描原始 CT 目录，识别哪些文件是真正的 DICOM 图像",
    "按扫描序列自动分组，把同一组切片放在一起",
    "跳过定位像、剂量报告、患者方案和切片太少的序列",
    "输出 .nii.gz 三维影像、preview 预览图和 manifest 清单",
  ], C.cyan);
  statTable(s, 0.68, 4.05,
    ["指标", "结果"],
    [
      ["识别 DICOM", "1001 个"],
      ["获得 series", "11 个"],
      ["转换成功", "8 个"],
      ["失败", "0 个"],
      ["主用序列", "0.80mm Hr40 / Hr60"],
    ],
    [1.45, 2.3],
    { rowH: 0.34 });
  imageFrame(s, img.p1Preview, 5.38, 1.55, 7.15, 4.5, "转换后生成的 NIfTI 三视图预览", "contain");
  explainNote(s, 5.38, 6.15, 7.15, 0.78, "白话解释",
    "DICOM 可以理解成“一摞带坐标的 CT 小切片”，每张图都知道自己在脑袋里的位置。NIfTI 则是把这些切片按正确顺序和间距叠成一个三维脑部 CT。后面的颅骨、血肿、脑室、路径规划，都必须建立在这个三维数据上。", C.cyan);
  speaker(s, [
    "这一步是所有后续处理的地基。医院 CT 导出的原始数据通常是一堆 DICOM 切片，每张切片都有自己的空间位置。",
    "程序做的事是先把同一个扫描序列的切片找出来，再按正确顺序、间距和方向叠成一个三维体数据，也就是 NIfTI 文件。",
    "这一步还会自动跳过定位像、患者方案、切片数太少的序列，因为这些东西不能组成稳定的三维脑部模型。转换成功后会生成 preview 图，先用肉眼看拼接是否正常。"
  ]);
}

// 6. Skull
{
  const s = addSlideBase(7, "步骤 2 · 颅骨分割", "目的：先找到头骨外壳，为颅腔提取和入颅点采样打基础", "STEP 2");
  imageFrame(s, img.skullOverlay, 0.68, 1.55, 7.0, 2.55, "颅骨 overlay：白/红色区域为骨结构", "contain");
  imageFrame(s, img.skull3d, 8.0, 1.55, 4.45, 4.95, "颅骨 3D 表面渲染", "contain");
  bulletBlock(s, 0.68, 4.35, 3.25, 1.45, "方法", [
    "骨头在 CT 上非常亮，用 HU 阈值先筛出疑似骨头",
    "用开闭运算去掉小噪点，并尽量补上骨缝小断裂",
    "保留最大的三维连通结构，避免把零散亮点当成颅骨",
  ], C.green);
  statTable(s, 4.15, 4.35,
    ["病例", "颅骨体积"],
    [
      ["0099039449", "402.10 cm³"],
      ["0100297683", "484.34 cm³"],
    ],
    [1.4, 1.3],
    { rowH: 0.36 });
  explainNote(s, 0.68, 5.95, 7.0, 0.92, "为什么先找骨头",
    "头骨像一个坚硬外壳。先把外壳找出来，程序才能知道“脑袋里面”大概在哪里；后面提取颅腔、判断路径是否穿过太厚的骨头、从颅骨表面采样入颅点，都要依赖这一步。overlay 图用于看二维叠加效果，3D 图用于直观看整体骨壳形状。", C.green);
  speaker(s, [
    "颅骨分割的核心逻辑很直观：骨头在 CT 上特别亮，所以可以用比较高的 HU 阈值先把疑似骨头筛出来。",
    "筛出来以后还要清理，因为 CT 里会有零散亮点、骨缝、小断裂。脚本会做形态学开闭运算，并保留最大的三维连通结构。",
    "为什么这一步重要？因为头骨是边界。后面要从骨头内部推颅腔，要从骨头表面找入颅点，还要判断路径穿过骨头的厚度，都离不开颅骨 mask。"
  ]);
}

// 7. Brain hematoma
{
  const s = addSlideBase(8, "步骤 3 · 颅腔、脑组织、血肿粗分割", "目的：定位脑内空间，并筛出疑似出血区域", "STEP 3");
  imageFrame(s, img.brainOverlay, 0.68, 1.52, 7.1, 2.72, "脑/血肿 overlay：红色为疑似血肿", "contain");
  imageFrame(s, img.brain3d, 8.08, 1.52, 4.35, 4.9, "颅骨半透明 + 血肿 3D", "contain");
  bulletBlock(s, 0.68, 4.58, 3.75, 1.65, "处理逻辑", [
    "先把颅骨围起来的内部空间填出来，得到大致颅腔",
    "在颅腔里用 HU 0-80 提取脑组织和类似软组织",
    "再用 HU 45-80 找更亮的疑似出血，并按体积、距离和形状过滤",
  ], C.red);
  statTable(s, 4.7, 4.58,
    ["病例", "颅腔", "脑组织", "血肿"],
    [
      ["0099039449", "827.24", "785.40", "8.37"],
      ["0100297683", "1048.32", "999.50", "0.00"],
    ],
    [1.15, 0.78, 0.78, 0.78],
    { align: ["left", "right", "right", "right"], rowH: 0.36 });
  s.addText("单位：mL；血肿为粗分割结果，需要人工复核。", { x: 4.72, y: 5.9, w: 3.4, h: 0.16, margin: 0, fontFace: FONT, fontSize: 6.5, color: C.muted });
  explainNote(s, 0.68, 6.12, 7.1, 0.78, "白话逻辑",
    "这一步像是在三维 CT 上分三层涂色：第一层先圈出头骨里面的空间，第二层把脑组织涂出来，第三层在脑组织里找更亮、形状更像出血的区域。本例 0099039449 检出约 8.37 mL 疑似血肿；但钙化、骨边缘伪影也可能偏亮，所以必须人工看 overlay 复核。", C.red);
  speaker(s, [
    "这一步是项目最核心的分割步骤。先用颅骨 mask 做填洞，把头骨里面的空间估计出来，这就是大致颅腔。",
    "然后在颅腔里按 HU 范围找脑组织。血肿通常比普通脑组织更亮，所以再用更高一点的 HU 范围筛出疑似出血区域。",
    "但是只靠亮度会有误判，比如钙化、骨头边缘伪影、图像噪声也可能很亮。所以脚本还加了体积、距骨距离、形状等过滤条件。当前病例 0099039449 得到 8.37 mL 疑似血肿，另一个病例没有检出血肿。"
  ]);
}

// 8. Ventricle
{
  const s = addSlideBase(9, "步骤 4 · 脑室分割", "目的：找出脑室区域，作为路径规划时需要避开的结构", "STEP 4");
  imageFrame(s, img.ventOverlay, 0.68, 1.52, 7.0, 2.72, "青色区域为脑室候选", "contain");
  imageFrame(s, img.vent3d, 8.02, 1.52, 4.42, 4.9, "颅骨 + 脑室 + 血肿 3D", "contain");
  bulletBlock(s, 0.68, 4.6, 3.9, 1.58, "方法", [
    "在颅腔内部寻找 HU 偏低、接近脑脊液的区域",
    "排除太靠近颅骨的低密度区域，减少把脑沟误当脑室",
    "按连通域大小筛选，保留更像脑室的主要区域",
  ], C.cyan);
  statTable(s, 4.88, 4.6,
    ["病例", "脑室体积", "连通域"],
    [
      ["0099039449", "19.09 mL", "5"],
      ["0100297683", "40.29 mL", "4"],
    ],
    [1.25, 1.25, 0.8],
    { rowH: 0.36 });
  explainNote(s, 0.68, 6.13, 7.0, 0.78, "为什么要避开脑室",
    "脑室可以理解成脑内装脑脊液的空腔，CT 上比普通脑组织更暗。路径规划时如果直线穿过脑室，可能增加风险或不符合预期，因此脚本把脑室外扩一定安全边距后作为禁区。本次两个病例都提取到了脑室候选区域。", C.cyan);
  speaker(s, [
    "脑室可以简单理解为脑子里面装脑脊液的空腔。它在 CT 上通常比普通脑组织更暗，所以脚本会在颅腔内寻找低 HU 区域。",
    "但低 HU 区域不一定都是脑室，靠近颅骨的脑沟或蛛网膜下腔也可能偏低，所以程序会排除太靠近颅骨的区域。",
    "脑室 mask 的作用主要是路径避让。路径规划时会把脑室外扩一定安全距离，如果候选路径穿过这个区域，就会被拒绝。"
  ]);
}

// 9. Vessel
{
  const s = addSlideBase(10, "步骤 5 · 血管风险禁区", "目的：在平扫 CT 无法精确分割血管时，生成保守避让区", "STEP 5");
  imageFrame(s, img.vesselOverlay, 0.68, 1.52, 7.0, 2.72, "红/橙色风险区用于路径避让", "contain");
  imageFrame(s, img.vessel3d, 8.02, 1.52, 4.42, 4.9, "风险区 3D 展示", "contain");
  bulletBlock(s, 0.68, 4.6, 4.1, 1.58, "关键说明", [
    "这一步不是精确血管分割，而是保守风险建模",
    "普通平扫 CT 很难看到未钙化血管，真实血管最好用 CTA/MRA",
    "脚本把高密度结构、中线静脉窦、大脑镰和颅底附近区域当作避让区",
  ], C.orange);
  statTable(s, 5.02, 4.6,
    ["病例", "风险区体积"],
    [
      ["0099039449", "77.76 mL"],
      ["0100297683", "213.37 mL"],
    ],
    [1.35, 1.35],
    { rowH: 0.36 });
  s.addText("后续接入 CTA/MRA 后应替换为真实血管分割。", { x: 5.05, y: 5.68, w: 2.85, h: 0.25, margin: 0, fontFace: FONT, fontSize: 7.4, color: C.muted, fit: "shrink" });
  explainNote(s, 0.68, 6.12, 7.0, 0.8, "重要边界",
    "普通平扫 CT 看不清很多血管，所以这页不能说“程序把血管都找出来了”。更准确的说法是：程序画出一些可能有血管风险、路径应尽量避开的区域。这样做会牺牲一部分可选路径，但能让规划更保守。后续若有 CTA/MRA，应替换为真实血管分割。", C.orange);
  speaker(s, [
    "这一页一定要讲清楚边界：普通平扫 CT 不是专门看血管的检查，很多血管在里面看不清。",
    "所以这个脚本不是精确血管分割，而是保守风险禁区。它会把一些高密度结构、中线附近的静脉窦区域、大脑镰区域、颅底附近风险区域画出来。",
    "对路径规划来说，这些区域相当于地图上的危险区。程序宁可少给一些路径，也尽量不让路径穿过这些可能有风险的位置。"
  ]);
}

// 10. Brainstem and eloquent
{
  const s = addSlideBase(11, "步骤 6-7 · 脑干与功能区近似禁区", "目的：把高风险结构纳入路径规划约束，但当前是几何近似", "STEP 6-7");
  imageFrame(s, img.brainstemOverlay, 0.68, 1.52, 5.95, 2.58, "脑干近似禁区 overlay", "contain");
  imageFrame(s, img.eloquentOverlay, 6.82, 1.52, 5.7, 2.58, "功能区近似禁区 overlay", "contain");
  bulletBlock(s, 0.68, 4.45, 3.7, 1.63, "脑干近似", [
    "脑干大致位于颅腔下部、中线附近，是需要重点避让的结构",
    "平扫 CT 上边界不清，脚本只能按位置、HU 范围和连通域估算",
    "输出用于路径规划避让，不等同于精确脑干分割",
  ], C.orange);
  bulletBlock(s, 4.65, 4.45, 3.7, 1.63, "功能区近似", [
    "按颅腔大小把坐标归一化，再按经验比例画禁区",
    "覆盖运动区、语言区、视觉区和深部核团等关键区域",
    "真实个体功能区需要 MRI、fMRI、DTI 或图谱配准支持",
  ], C.purple);
  statTable(s, 8.62, 4.45,
    ["病例", "脑干", "功能区"],
    [
      ["0099039449", "20.89", "185.52"],
      ["0100297683", "36.70", "204.56"],
    ],
    [1.2, 0.9, 0.9],
    { align: ["left", "right", "right"], rowH: 0.36 });
  s.addText("单位：mL", { x: 8.66, y: 5.48, w: 1.4, h: 0.14, margin: 0, fontFace: FONT, fontSize: 6.5, color: C.muted });
  explainNote(s, 0.68, 6.1, 11.85, 0.78, "为什么叫“近似禁区”",
    "脑干和功能区都非常重要，但平扫 CT 对这些结构的显示能力有限。当前脚本并不是“看见了每个人真实的功能区”，而是按解剖经验画一个保守范围，让路径规划尽量不穿过。汇报时要强调：这是避让约束，不是可直接用于临床定位的精准分割。", C.purple);
  speaker(s, [
    "脑干和功能区都是路径规划里必须谨慎对待的结构，但它们在平扫 CT 上并不像骨头和明显出血那样容易看清。",
    "脑干近似是根据颅腔下部、中线附近的位置估算出来的。功能区近似则是把颅腔坐标归一化后，按经验位置画出运动区、语言区、视觉区和深部核团。",
    "汇报时要避免说这是精确分割。更准确的说法是：这是为了路径规划做的保守避让区，真实功能区定位需要 MRI、fMRI、DTI 或图谱配准。"
  ]);
}

// 11. Path planning
{
  const s = addSlideBase(12, "步骤 8 · 穿刺路径规划", "目的：在避开禁区的前提下，从颅骨外表面找到到达血肿的候选直线路径", "STEP 8");
  imageFrame(s, img.pathsOverlay, 0.68, 1.52, 7.0, 2.65, "路径 overlay：线条为候选路径，黄星为目标点", "contain");
  imageFrame(s, img.paths3d, 8.05, 1.52, 4.38, 4.9, "路径规划 3D", "contain");
  bulletBlock(s, 0.68, 4.48, 3.8, 1.58, "路径评估规则", [
    "从颅骨外表面采样 1500 个可能的入颅点",
    "目标点不只取血肿中心，还沿血肿长轴取远端点",
    "每条直线都要检查骨厚、颅腔比例和禁区碰撞",
  ], C.green);
  statTable(s, 4.72, 4.48,
    ["路径", "目标", "长度", "角度"],
    [
      ["Top-1", "质心", "50.6 mm", "18.1°"],
      ["Top-2", "远端 A", "92.4 mm", "75.1°"],
    ],
    [0.78, 0.92, 0.95, 0.75],
    { rowH: 0.36 });
  s.addText("候选入颅点 1500 个，最终合法路径 2 条；另一病例因血肿 mask 为空自动跳过。", {
    x: 4.74, y: 5.55, w: 3.08, h: 0.35, margin: 0, fontFace: FONT, fontSize: 7.1, color: C.muted, fit: "shrink",
  });
  explainNote(s, 0.68, 6.08, 7.0, 0.82, "筛选方式",
    "程序把入颅点和目标点两两连成直线，然后像沿着一根针往里走一样逐点检查：进颅骨头是否太厚、进入颅腔后是否又碰到骨头、路径是否主要在颅腔内、有没有穿过脑室/血管风险/脑干/功能区。只要违反规则就丢弃，剩下的按长度和角度打分。", C.green);
  speaker(s, [
    "路径规划可以分成四步讲。第一步是在颅骨外表面采样很多候选入颅点，这次最多采了 1500 个。",
    "第二步是确定目标点。它不只取血肿中心，还沿血肿长轴取远端点，这样可以覆盖血肿的形状。",
    "第三步是逐条检查直线。每条线都要检查骨头厚度、是否重复碰骨、是否大部分在颅腔内、是否穿过脑室、血管风险、脑干和功能区。第四步才是对剩余合法路径打分，按更短、更合适的角度排序。"
  ]);
}

// 12. Report generation
{
  const s = addSlideBase(13, "步骤 9 · 自然语言方案与 PDF 报告", "目的：把分割图、统计结果和路径规划结果整理成可交付材料", "STEP 9");
  imageFrame(s, img.pathsOverlay, 0.7, 1.55, 6.25, 2.45, "报告中的路径主页图", "contain");
  bulletBlock(s, 7.25, 1.55, 4.85, 1.4, "自动生成文件", [
    "每个病例生成 plan.txt",
    "每个病例生成 report.pdf",
    "PDF 包含封面、方案、路径图和各分割页",
  ], C.cyan);
  card(s, 7.25, 3.22, 4.85, 1.78, { fill: C.panel2, line: C.green });
  s.addText("推荐路径摘要（病例 0099039449）", { x: 7.48, y: 3.46, w: 4.25, h: 0.2, margin: 0, fontFace: FONT, fontSize: 10.4, bold: true, color: C.green });
  s.addText("入颅点 LPS: (77.57, -143.12, -513.71) mm\n目标点 LPS: (33.24, -166.86, -507.71) mm\n进针长度: 50.6 mm    入颅角度: 18.1°", {
    x: 7.48, y: 3.86, w: 4.1, h: 0.62, margin: 0, fontFace: FONT, fontSize: 8.4, color: C.ink, breakLine: false, fit: "shrink",
  });
  bulletBlock(s, 0.7, 4.42, 6.25, 1.42, "报告价值", [
    "把很多零散输出统一成医生、老师或评审更容易浏览的材料",
    "PNG 负责展示效果，JSON 保留坐标、体积、评分等机器可读数据",
    "PDF 和 plan.txt 适合作为阶段性汇报、人工检查和归档入口",
  ], C.green);
  statTable(s, 7.25, 5.22,
    ["病例", "plan.txt", "report.pdf"],
    [
      ["0099039449", "已生成", "已生成"],
      ["0100297683", "已生成", "已生成"],
    ],
    [1.35, 1.2, 1.2],
    { rowH: 0.36 });
  explainNote(s, 0.7, 6.2, 11.4, 0.68, "文件怎么用",
    ".nii.gz 是三维影像和 mask，适合放进 3D Slicer 继续检查；.png 是给人快速看每一步效果；.json 记录体积、坐标、路径评分和拒绝原因；.txt/.pdf 把这些内容翻译成更适合阅读和汇报的形式。", C.cyan);
  speaker(s, [
    "前面步骤会生成很多技术文件，直接看目录会比较乱，所以最后做了文字方案和 PDF 报告。",
    ".nii.gz 文件是给软件继续打开和分析的，比如可以放进 3D Slicer 看三维位置。.png 是给人快速检查效果的，比如看 overlay 有没有明显错分。.json 是给程序和开发人员看的，里面有坐标、体积、评分、拒绝原因。",
    "plan.txt 和 report.pdf 是汇报友好的格式，把路径、体积、图像和关键统计统一整理出来。这里的方案仍然只是自动生成的参考文字，不是医生最终方案。"
  ]);
}

// 13. Limitations
{
  const s = addSlideBase(14, "局限性与下一步计划", "当前系统已经跑通流程，但距离临床可用还需要数据、模型和验证体系", "NEXT");
  const items = [
    ["医学边界", "平扫 CT 对骨头和明显出血较友好，但血管、功能区和脑干边界显示有限；相关模块目前是保守近似。", C.red],
    ["算法边界", "血肿分割主要依赖 HU 阈值和形态学规则，可能把钙化、骨边缘伪影误认为血肿，也可能漏掉不典型出血。", C.orange],
    ["规划边界", "路径只评估直线和 mask 碰撞，未模拟皮肤、肌肉、骨缝、器械直径、血管变异和真实手术操作约束。", C.purple],
    ["工程边界", "当前是脚本流水线，适合原型验证；后续需要一键运行、日志、质控、交互式 3D 复核和批量评估。", C.cyan],
  ];
  items.forEach((it, i) => {
    const x = i % 2 === 0 ? 0.75 : 6.82;
    const y = i < 2 ? 1.65 : 3.55;
    card(s, x, y, 5.45, 1.35, { fill: C.panel2, line: it[2] });
    s.addText(it[0], { x: x + 0.25, y: y + 0.22, w: 1.45, h: 0.22, margin: 0, fontFace: FONT, fontSize: 11.5, bold: true, color: it[2] });
    s.addText(it[1], { x: x + 0.25, y: y + 0.62, w: 4.9, h: 0.4, margin: 0, fontFace: FONT, fontSize: 8.3, color: C.sub, fit: "shrink" });
  });
  bulletBlock(s, 0.75, 5.64, 11.55, 0.98, "建议推进顺序", [
    "人工复核当前 2 例 overlay 和 3D 图 → 接入更多病例与医生标注 → 用真实 CTA/MRI 或训练模型替换近似模块 → 增加 3D Slicer 复核流程 → 建立 Dice、误检率、路径碰撞率等定量指标",
  ], C.green);
  speaker(s, [
    "最后要主动讲局限性，这样汇报更稳。当前流程已经跑通，但它主要依赖规则、阈值和几何经验，不是经过大量标注数据验证的临床系统。",
    "医学上，平扫 CT 对血管、功能区和脑干边界显示有限；算法上，血肿阈值分割可能误判；规划上，目前只考虑直线路径和 mask 碰撞，没有模拟真实手术器械和个体血管变异。",
    "下一步建议是先人工复核当前两例所有 overlay 和 3D 图，再接入更多病例和医生标注；对近似模块，优先用 CTA/MRI 或训练模型替换；最后建立定量指标，证明分割和路径规划到底有多可靠。"
  ]);
}

// PDF report appendix
reportPageImages.forEach((pagePath, i) => {
  const idx = 15 + i;
  const total = reportPageImages.length;
  const s = addSlideBase(idx, `附录 · 病例 PDF 报告（${i + 1}/${total}）`, "病例 0099039449 的自动生成报告已按页插入 PPT，便于汇报时直接翻阅", "PDF REPORT");
  imageFrame(s, pagePath, 0.78, 1.4, 11.8, 5.42, `CT_brain_0.80_Hr40_S3_00000262_report.pdf · 第 ${i + 1} 页`, "contain");
  speaker(s, [
    `这是病例 0099039449 自动生成 PDF 报告的第 ${i + 1} 页，共 ${total} 页。`,
    "这里插入的是 PDF 页面渲染后的图片，作用是让汇报文件本身包含完整报告内容，不需要现场再单独打开 PDF。",
    "如果后续重新生成 PDF，需要重新把 PDF 页面导出成图片并重新生成 PPT，才能同步更新附录页。"
  ]);
});

// Basic concepts
if (false) {
{
  const s = addSlideBase(14, "汇报前先讲清楚 4 个基础概念", "这页用于让非医学背景听众理解后续每一步产物", "EXPLAINER");
  paragraphBlock(s, 0.72, 1.5, 2.85, 2.1, "DICOM", "医院 CT 机器导出的原始影像格式。它不是普通照片，而是一组切片文件，每个文件还带有患者、扫描参数和空间位置等信息。", C.cyan);
  paragraphBlock(s, 3.82, 1.5, 2.85, 2.1, "NIfTI", "医学影像分析常用的三维体数据格式，后缀通常是 .nii.gz。可以理解为把一堆 CT 切片按正确位置叠成一个三维脑部模型。", C.green);
  paragraphBlock(s, 6.92, 1.5, 2.85, 2.1, "Mask", "mask 是“涂色图层”。例如颅骨 mask 里，骨头位置标成 1，其他地方标成 0，程序就能知道哪里是骨头。", C.purple);
  paragraphBlock(s, 10.02, 1.5, 2.55, 2.1, "HU 值", "CT 里每个点都有亮度数值。空气低，水和脑脊液偏低，脑组织居中，出血更亮，骨头最亮。项目大量依赖这些范围。", C.orange);
  bulletBlock(s, 0.72, 4.05, 5.78, 1.55, "为什么这些概念重要", [
    "DICOM 是输入，NIfTI 是算法可处理的三维影像",
    "mask 是所有分割结果和路径避让的基础",
    "HU 阈值是当前规则算法的核心，但它不是百分百准确",
  ], C.cyan);
  imageFrame(s, img.p1Preview, 6.82, 4.05, 5.75, 1.9, "DICOM 转换后的三视图预览示例", "contain");
}

// 15. Step input-output table A
{
  const s = addSlideBase(15, "步骤 1-4 的输入、目的和输出", "前半段重点是把原始数据变成可分析结构，并找出血肿和脑室", "DETAILS");
  statTable(s, 0.55, 1.5,
    ["步骤", "输入", "核心目的", "主要输出"],
    [
      ["1 DICOM 转 NIfTI", "原始 DICOM 切片", "把散落切片拼成三维 CT", ".nii.gz / preview / manifest"],
      ["2 颅骨分割", "Hr60 骨重建 CT", "找到头骨外壳，供后续定位", "skull_mask / overlay / 3D"],
      ["3 脑+血肿", "Hr40 CT + 颅骨 mask", "提取颅腔、脑组织和疑似血肿", "brain_mask / hematoma_mask / report"],
      ["4 脑室分割", "CT + 颅腔 + 颅骨", "找到路径需要避开的脑室区域", "ventricle_mask / stats / 3D"],
    ],
    [1.55, 2.15, 3.25, 3.15],
    { rowH: 0.58, headH: 0.42 });
  bulletBlock(s, 0.75, 4.75, 5.65, 1.45, "这一阶段的汇报口径", [
    "先把数据格式统一，再逐步生成结构 mask",
    "颅骨是后续颅腔提取和入颅点采样的基础",
    "血肿分割是粗筛结果，必须看 overlay 图复核",
  ], C.green);
  bulletBlock(s, 6.72, 4.75, 5.65, 1.45, "当前跑出的关键结果", [
    "成功转换 8 个体数据序列，失败 0",
    "2 个病例均完成颅骨、脑组织和脑室分割",
    "病例 0099039449 检出疑似血肿 8.37 mL",
  ], C.red);
}

// 16. Step input-output table B
{
  const s = addSlideBase(16, "步骤 5-9 的输入、目的和输出", "后半段重点是建立避让约束，寻找路径，并生成交付报告", "DETAILS");
  statTable(s, 0.55, 1.42,
    ["步骤", "输入", "核心目的", "主要输出"],
    [
      ["5 血管风险", "CT + 颅腔 + 血肿", "平扫 CT 下生成保守避让区", "vessel_risk_mask / stats"],
      ["6 脑干近似", "CT + 颅腔 + 脑组织", "估算脑干禁区，路径规划避让", "brainstem_mask / overlay"],
      ["7 功能区近似", "颅腔 + 脑组织", "按经验位置画功能区禁区", "eloquent masks / stats"],
      ["8 路径规划", "颅骨 + 血肿 + 禁区 masks", "找短、直、避开风险区的候选路径", "paths.json / overlay / 3D"],
      ["9 报告生成", "PNG + JSON 结果", "整理为可阅读方案和 PDF", "plan.txt / report.pdf"],
    ],
    [1.55, 2.25, 3.15, 3.15],
    { rowH: 0.5, headH: 0.42 });
  paragraphBlock(s, 0.75, 5.18, 5.65, 1.25, "为什么要做风险禁区", "路径规划不是只追求最短。如果直线穿过脑室、脑干、功能区或血管风险区域，即使很短也会被拒绝。当前风险区多数是保守近似，作用是帮助程序“宁可绕开，也不冒险”。", C.orange);
  paragraphBlock(s, 6.72, 5.18, 5.65, 1.25, "为什么还要生成报告", "算法结果分散在 mask、PNG、JSON 中，直接阅读成本很高。报告生成步骤把路径、体积、坐标、叠加图统一整理，便于人工复核、项目汇报和后续归档。", C.cyan);
}

// 17. From skull to hematoma
{
  const s = addSlideBase(17, "从颅骨到血肿：核心分割逻辑白话解释", "这页用于讲清楚项目最核心的“找血肿”过程", "EXPLAINER");
  imageFrame(s, img.brainOverlay, 0.68, 1.42, 6.6, 2.48, "脑组织与血肿 overlay", "contain");
  paragraphBlock(s, 7.55, 1.42, 4.85, 1.12, "第一步：由骨头推颅腔", "先找到头骨壳，再把壳里面的空间填出来，减掉骨头本身，得到大致颅腔。", C.green);
  paragraphBlock(s, 7.55, 2.72, 4.85, 1.12, "第二步：在颅腔里筛脑组织", "用 HU 0-80 的范围提取脑组织和类似软组织区域。血肿通常也在这个范围内，所以会被包含进去。", C.cyan);
  paragraphBlock(s, 7.55, 4.02, 4.85, 1.18, "第三步：筛疑似血肿", "新鲜出血通常更亮，项目用 HU 45-80 作为候选，再用体积、距骨距离和形状实心度过滤明显误判。", C.red);
  bulletBlock(s, 0.68, 4.35, 6.6, 1.45, "这一步的注意事项", [
    "血肿结果是“粗分割”，不是最终诊断",
    "钙化、骨边缘伪影等也可能偏亮，需要人工看图确认",
    "本例检出 1 个疑似血肿，体积约 8.37 mL",
  ], C.red);
}

// 18. Approximation zones
{
  const s = addSlideBase(18, "为什么血管、脑干、功能区只能做近似", "这页用于避免听众误解：这些不是精准医学分割，而是路径规划避让用的保守区域", "EXPLAINER");
  imageFrame(s, img.vesselOverlay, 0.65, 1.42, 3.8, 2.05, "血管风险区", "contain");
  imageFrame(s, img.brainstemOverlay, 4.75, 1.42, 3.8, 2.05, "脑干近似区", "contain");
  imageFrame(s, img.eloquentOverlay, 8.85, 1.42, 3.8, 2.05, "功能区近似区", "contain");
  paragraphBlock(s, 0.65, 3.82, 3.8, 1.55, "血管风险", "普通平扫 CT 看不清很多未钙化血管，因此脚本用高密度结构和解剖先验画出“可能危险”的避让区。", C.orange);
  paragraphBlock(s, 4.75, 3.82, 3.8, 1.55, "脑干近似", "脑干在 CT 上和周围脑组织灰度接近，脚本只能根据颅腔下部、中线附近的位置估算禁区。", C.red);
  paragraphBlock(s, 8.85, 3.82, 3.8, 1.55, "功能区近似", "真正功能区需要 MRI、fMRI 或图谱配准；当前只是按经验位置画运动、语言、视觉和深部核团避让区。", C.purple);
  bulletBlock(s, 0.65, 5.82, 12.0, 0.65, "汇报时建议这样说", [
    "这些 mask 的用途是路径规划避让，不应被称为真实血管或真实功能区分割；后续有 CTA/MRI 后应替换为更精确的模型。",
  ], C.cyan);
}

// 19. Path planning details
{
  const s = addSlideBase(19, "路径规划是怎样筛出 Top 路径的", "从 1500 个候选入颅点出发，逐条直线检查，最后只保留合法路径", "EXPLAINER");
  imageFrame(s, img.pathsOverlay, 0.68, 1.42, 6.6, 2.52, "路径 overlay", "contain");
  const flow = [
    ["1", "采样入颅点", "从颅骨外表面取 1500 个候选点，排除面部和颅底区域。", C.cyan],
    ["2", "采样目标点", "沿血肿 PCA 长轴取远端、质心、远端三个目标点。", C.green],
    ["3", "逐线检查", "每条入颅点到目标点的直线都检查骨厚、颅腔比例和禁区碰撞。", C.orange],
    ["4", "排序输出", "按路径长度和入颅角度打分，越短、越接近合适角度越优。", C.purple],
  ];
  flow.forEach((f, i) => {
    const x = 7.55;
    const y = 1.38 + i * 1.08;
    card(s, x, y, 4.85, 0.88, { fill: C.panel2, line: f[3] });
    s.addShape(pptx.ShapeType.ellipse, { x: x + 0.2, y: y + 0.2, w: 0.38, h: 0.38, fill: { color: f[3] }, line: { color: f[3], transparency: 100 } });
    s.addText(f[0], { x: x + 0.2, y: y + 0.29, w: 0.38, h: 0.1, margin: 0, fontFace: FONT, fontSize: 7.2, color: C.bg, bold: true, align: "center" });
    s.addText(f[1], { x: x + 0.72, y: y + 0.14, w: 1.4, h: 0.16, margin: 0, fontFace: FONT, fontSize: 8.5, color: f[3], bold: true });
    s.addText(f[2], { x: x + 0.72, y: y + 0.39, w: 3.75, h: 0.24, margin: 0, fontFace: FONT, fontSize: 7.2, color: C.sub, fit: "shrink" });
  });
  statTable(s, 0.68, 4.42,
    ["指标", "本次结果"],
    [
      ["候选入颅点", "1500 个"],
      ["合法路径", "2 条"],
      ["Top-1 长度", "50.6 mm"],
      ["Top-1 入颅角度", "18.1°"],
      ["跳过病例", "1 个：血肿 mask 为空"],
    ],
    [1.55, 1.65],
    { rowH: 0.34 });
  paragraphBlock(s, 4.15, 4.42, 3.15, 1.55, "如何解读 Top-2", "第二条路径虽然合法，但长度 92.4 mm、角度 75.1°，说明它更多是备选记录，实际参考价值弱于 Top-1。", C.orange);
}

// 20. Output interpretation
{
  const s = addSlideBase(20, "如何解读最终输出文件", "这页可以作为汇报结尾或答疑页，说明每类文件给谁看、怎么用", "OUTPUT");
  statTable(s, 0.72, 1.42,
    ["文件类型", "典型文件", "用途"],
    [
      [".nii.gz", "CT / mask 三维文件", "给 3D Slicer 或程序继续分析"],
      [".png", "preview / overlay / 3D", "给人快速检查每一步效果"],
      [".json", "brain_report / paths", "保存体积、坐标、路径评分等结构化数据"],
      [".txt", "plan.txt", "自然语言手术路径建议，便于阅读"],
      [".pdf", "report.pdf", "整合版病例报告，适合归档和汇报"],
    ],
    [1.2, 2.25, 6.2],
    { rowH: 0.48, headH: 0.42 });
  paragraphBlock(s, 0.72, 4.62, 3.7, 1.5, "对医生/评审", "优先看 overlay 图和 PDF 报告，判断分割是否明显错误，路径是否穿过不合理区域。", C.green);
  paragraphBlock(s, 4.75, 4.62, 3.7, 1.5, "对算法开发", "重点看 JSON 里的体积、连通域、拒绝统计和路径评分，用于调参和后续算法替换。", C.cyan);
  paragraphBlock(s, 8.78, 4.62, 3.6, 1.5, "对项目管理", "这套输出说明流程已跑通，但还需要人工标注、更多病例和定量评估，才能证明可靠性。", C.orange);
}
}

await pptx.writeFile({ fileName: OUT });
console.log(OUT);
