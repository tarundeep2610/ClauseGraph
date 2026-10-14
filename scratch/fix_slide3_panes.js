const fs = require('fs');

const filePath = 'OCR Studio \u2014 Streamlined UX Redesign Mockups.html';
let content = fs.readFileSync(filePath, 'utf8');

// Match Slide 3 section
const m3 = content.match(/id=["']slide-3["'][\s\S]*?<iframe[^>]*srcdoc="([\s\S]*?)"[\s\S]*?<\/iframe>/i);
if (!m3) {
  console.error('Slide 3 iframe not found!');
  process.exit(1);
}

let s3Srcdoc = m3[1];

// 1. In paneScan:
// Replace scan header and banner with clean Page 14 header:
const oldScanPart = `      &lt;section id=&quot;paneScan&quot; class=&quot;pane-scan&quot;&gt;
        &lt;div class=&quot;pane-quiet-header&quot;&gt;
          &lt;span&gt;Original Scan (Page 3)&lt;/span&gt;
          &lt;span style=&quot;font-size:11px; font-weight:400; color:var(--text-subtle);&quot;&gt;300 DPI Pristine&lt;/span&gt;
        &lt;/div&gt;
        &lt;div style=&quot;background:var(--bg-subtle); border-bottom:1px solid var(--border); padding:5px 14px; display:flex; align-items:center; justify-content:space-between; gap:10px;&quot;&gt;
          &lt;span style=&quot;font-size:11px; font-weight:600; color:var(--text-subtle);&quot;&gt;📄 OCR Text Ready. Want automatic clause citations &amp;amp; outline?&lt;/span&gt;
          &lt;button onclick=&quot;window.parent.goToSlideById(&#x27;slide-4&#x27;)&quot; style=&quot;background:var(--primary); color:#ffffff; border:none; font-size:11px; font-weight:700; padding:3px 10px; border-radius:6px; cursor:pointer;&quot;&gt;⚡ Build Index &amp;amp; Citations&lt;/button&gt;
        &lt;/div&gt;`;

const newScanPart = `      &lt;section id=&quot;paneScan&quot; class=&quot;pane-scan&quot;&gt;
        &lt;div class=&quot;pane-quiet-header&quot;&gt;
          &lt;span&gt;Original Scan (Page 14)&lt;/span&gt;
          &lt;span style=&quot;color:var(--warning); font-size:11px; font-weight:700;&quot;&gt;Needs Review · 71%&lt;/span&gt;
        &lt;/div&gt;`;

// 2. In paneEditor:
// Add quiet header before editor-ribbon:
const oldEditorPart = `      &lt;section id=&quot;paneEditor&quot; class=&quot;pane-editor&quot;&gt;
        &lt;div class=&quot;editor-ribbon&quot;&gt;`;

const newEditorPart = `      &lt;section id=&quot;paneEditor&quot; class=&quot;pane-editor&quot;&gt;
        &lt;div class=&quot;pane-quiet-header&quot;&gt;
          &lt;span&gt;Extracted Text (Page 14)&lt;/span&gt;
          &lt;div style=&quot;display:flex; gap:6px;&quot;&gt;
            &lt;button style=&quot;background:var(--bg-subtle); border:1px solid var(--border-strong); color:var(--text-main); padding:3px 8px; border-radius:5px; font-size:11px; font-weight:700; cursor:pointer;&quot; title=&quot;Admin only: Rerun OCR extraction for this page&quot;&gt;
              ⚡ Rerun &lt;span style=&quot;font-size:9.5px; opacity:0.75; font-weight:600; background:var(--border); padding:1px 4px; border-radius:3px;&quot;&gt;Admin&lt;/span&gt;
            &lt;/button&gt;
            &lt;button style=&quot;background:var(--success); color:#fff; border:none; padding:3px 9px; border-radius:5px; font-size:11px; font-weight:700; cursor:pointer;&quot; title=&quot;Save corrections and move to next flagged page&quot;&gt;
              ✓ Mark Verified &amp;amp; Next
            &lt;/button&gt;
          &lt;/div&gt;
        &lt;/div&gt;
        &lt;div class=&quot;editor-ribbon&quot;&gt;`;

const normSrcdoc = s3Srcdoc.replace(/\r\n/g, '\n');
const normScan = oldScanPart.replace(/\r\n/g, '\n');
const normEditor = oldEditorPart.replace(/\r\n/g, '\n');

if (!normSrcdoc.includes(normScan)) {
  console.error('oldScanPart not found!');
  process.exit(1);
}
if (!normSrcdoc.includes(normEditor)) {
  console.error('oldEditorPart not found!');
  process.exit(1);
}

let updatedSrcdoc = normSrcdoc.replace(normScan, newScanPart.replace(/\r\n/g, '\n'));
updatedSrcdoc = updatedSrcdoc.replace(normEditor, newEditorPart.replace(/\r\n/g, '\n'));

const oldIframe = m3[0];
const newIframe = oldIframe.replace(/srcdoc="[\s\S]*?"/i, `srcdoc="${updatedSrcdoc}"`);

content = content.replace(oldIframe, newIframe);

fs.writeFileSync(filePath, content, 'utf8');
console.log('Successfully fixed Slide 3 scan & editor headers!');
