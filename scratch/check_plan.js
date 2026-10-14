const fs = require('fs');
const content = fs.readFileSync('OCR_STUDIO_UX_REDESIGN_PLAN.md', 'utf8');

const lines = content.split(/\r?\n/);
lines.forEach((l, i) => {
  if (l.toLowerCase().includes('slide 3') || l.toLowerCase().includes('review') || l.toLowerCase().includes('queue') || l.toLowerCase().includes('description')) {
    console.log(`${i}: ${l}`);
  }
});
