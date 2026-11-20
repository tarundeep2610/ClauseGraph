const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m2 = content.match(/id=["']slide-2["'][\s\S]*?<\/section>/i);
const s2Style = m2[0].match(/&lt;style&gt;([\s\S]*?)&lt;\/style&gt;/i)[1];

const classes = [
  'task-rail', 'rail-btn-wrap', 'rail-icon-btn', 'rail-btn-label', 'rail-badge-alert',
  'floating-nav-hud', 'hud-cluster', 'hud-btn', 'hud-page-box', 'hud-page-input',
  'hud-page-total', 'hud-divider', 'hud-segmented', 'hud-seg-btn', 'split-resizer',
  'resizer-bar', 'resizer-pill', 'resizer-snaps', 'snap-btn', 'pane-scan',
  'pane-editor', 'editor-ribbon', 'ribbon-btn', 'ribbon-select', 'editor-viewport', 'editor-doc'
];

classes.forEach(c => {
  const has = s2Style.includes('.' + c) || s2Style.includes(c);
  console.log(`${c}: ${has ? 'YES' : 'MISSING'}`);
});
