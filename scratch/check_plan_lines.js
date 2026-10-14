const fs = require('fs');
const content = fs.readFileSync('OCR_STUDIO_UX_REDESIGN_PLAN.md', 'utf8');

const lines = content.split(/\r?\n/);
lines.forEach((l, i) => {
  if (l.includes('Feature 6: Guided Review Queue') || l.includes('ReviewQueuePanel') || l.includes('Slide 03') || l.includes('Guided Review Queue')) {
    console.log(`${i}: ${l}`);
  }
});
