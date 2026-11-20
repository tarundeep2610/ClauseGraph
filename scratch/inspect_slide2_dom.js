const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m2 = content.match(/id=['"]slide-2['"][\s\S]*?<\/section>/i);
if (m2) {
  const lines = m2[0].split(/\r?\n/);
  console.log('=== SLIDE 2 STRUCTURE ===');
  lines.forEach((l, i) => {
    if (l.includes('<header') || l.includes('class="studio-main-body"') || l.includes('<aside') || l.includes('class="workspace-panes"') || l.includes('class="pane-scan"') || l.includes('class="pane-editor"') || l.includes('class="pane-quiet-header"') || l.includes('floatingHud')) {
      console.log(`${i}: ${l}`);
    }
  });
}
