const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m2 = content.match(/id=['"]slide-2['"][\s\S]*?<\/section>/i);
if (m2) {
  const lines = m2[0].split(/\r?\n/);
  console.log('=== SLIDE 2 DOM ELEMENTS ===');
  lines.forEach((l, i) => {
    if (l.includes('&lt;header') || l.includes('studio-main-body') || l.includes('&lt;aside') || l.includes('workspace-panes') || l.includes('pane-scan') || l.includes('pane-editor') || l.includes('pane-quiet-header') || l.includes('studio-rail') || l.includes('rail-btn')) {
      console.log(`${i}: ${l}`);
    }
  });
}
