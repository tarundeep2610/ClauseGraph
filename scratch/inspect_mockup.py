import glob, sys, re, html
sys.stdout.reconfigure(encoding='utf-8')

files = glob.glob('*.html')
with open(files[0], 'r', encoding='utf-8') as f:
    text = f.read()

m = re.search(r'<section\s+id="slide-2"[^>]*>.*?srcdoc="([^"]+)"', text, re.DOTALL)
if m:
    slide2 = html.unescape(m.group(1))
    css_m = re.search(r'<style>(.*?)</style>', slide2, re.DOTALL)
    if css_m:
        css = css_m.group(1)
        # Find all selectors and rules
        for rule in re.findall(r'(\.[a-zA-Z0-9_,-.\s:>+]+?\{[^}]+?\})', css):
            sel = rule[:rule.find('{')].strip()
            if any(k in sel for k in ['top-file-bar', 'task-rail', 'rail', 'hud', 'search', 'resizer', 'split', 'dual']):
                print(rule)
                print()
