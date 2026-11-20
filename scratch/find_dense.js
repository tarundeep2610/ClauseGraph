const fs = require('fs');
const path = require('path');

function searchDir(dir) {
  const files = fs.readdirSync(dir);
  for (const f of files) {
    if (f.startsWith('.') || f === 'node_modules') continue;
    const full = path.join(dir, f);
    const stat = fs.statSync(full);
    if (stat.isDirectory()) {
      if (f !== 'scratch') searchDir(full);
    } else if (f.endsWith('.html') || f.endsWith('.js') || f.endsWith('.py') || f.endsWith('.md')) {
      try {
        const text = fs.readFileSync(full, 'utf8');
        if (text.includes('Dense Financial Table') || text.includes('Smudged Decimal Points')) {
          console.log(`FOUND in file: ${full}`);
        }
      } catch (e) {}
    }
  }
}

searchDir('.');
