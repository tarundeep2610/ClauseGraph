const fs = require('fs');

const targetPath = 'OCR Studio \u2014 Streamlined UX Redesign Mockups.html';
let content = fs.readFileSync(targetPath, 'utf8');

// Target chunk in Slide 3 pane-editor:
const oldEditorBlock = `&lt;section class=&quot;pane-editor&quot; style=&quot;flex:1;&quot;&gt;
        &lt;div class=&quot;pane-quiet-header&quot;&gt;
          &lt;span&gt;Extracted Text (Page 14)&lt;/span&gt;
          &lt;div style=&quot;display:flex; gap:6px;&quot;&gt;
            &lt;button style=&quot;background:var(--bg-subtle); border:1px solid var(--border-strong); color:var(--text-main); padding:3px 8px; border-radius:5px; font-size:11px; font-weight:700; cursor:pointer;&quot; title=&quot;Admin only: Rerun OCR extraction for this page&quot;&gt;
              ⚡ Rerun &lt;span style=&quot;font-size:9.5px; opacity:0.75; font-weight:600; background:var(--border); padding:1px 4px; border-radius:3px;&quot;&gt;Admin&lt;/span&gt;
            &lt;/button&gt;
            &lt;button style=&quot;background:var(--success); color:#fff; border:none; padding:3px 9px; border-radius:5px; font-size:11px; font-weight:700; cursor:pointer;&quot; title=&quot;General users proofread &amp;amp; edit text, then mark verified&quot;&gt;
              ✓ Mark Verified &amp;amp; Next
            &lt;/button&gt;
          &lt;/div&gt;
        &lt;/div&gt;
        &lt;div class=&quot;editor-viewport&quot;&gt;
          &lt;div class=&quot;editor-doc&quot; contenteditable=&quot;true&quot;&gt;
            &lt;h2&gt;Payment Milestone Schedule&lt;/h2&gt;
            &lt;p&gt;Milestone 1: $1,250,000.00 upon approval of DPR.&lt;/p&gt;
            &lt;p&gt;Milestone 2: $3,500,000.00 upon delivery of machinery.&lt;/p&gt;
          &lt;/div&gt;
        &lt;/div&gt;
      &lt;/section&gt;`;

const newEditorBlock = `&lt;section class=&quot;pane-editor&quot; style=&quot;flex:1;&quot;&gt;
        &lt;div class=&quot;pane-quiet-header&quot;&gt;
          &lt;div style=&quot;display:flex; align-items:center; gap:8px;&quot;&gt;
            &lt;span style=&quot;font-weight:700;&quot;&gt;Extracted Text (Page 14)&lt;/span&gt;
            &lt;button id=&quot;toggleEditBtn&quot; onclick=&quot;toggleEditMode()&quot; style=&quot;background:var(--primary-subtle); border:1px solid var(--primary-border); color:var(--primary); padding:2px 8px; border-radius:5px; font-size:11px; font-weight:700; cursor:pointer; display:flex; align-items:center; gap:5px;&quot; title=&quot;Toggle direct editing mode on/off&quot;&gt;
              &lt;span id=&quot;editDot&quot; style=&quot;width:6px; height:6px; border-radius:50%; background:var(--primary);&quot;&gt;&lt;/span&gt;
              &lt;span id=&quot;editLabel&quot;&gt;✎ Edit Mode: ON&lt;/span&gt;
            &lt;/button&gt;
            &lt;span id=&quot;saveStatus&quot; style=&quot;font-size:10.5px; color:var(--text-subtle); display:flex; align-items:center; gap:3px;&quot;&gt;● Autosaved&lt;/span&gt;
          &lt;/div&gt;
          &lt;div style=&quot;display:flex; gap:6px;&quot;&gt;
            &lt;button style=&quot;background:var(--bg-subtle); border:1px solid var(--border-strong); color:var(--text-main); padding:3px 8px; border-radius:5px; font-size:11px; font-weight:700; cursor:pointer;&quot; title=&quot;Admin only: Rerun OCR extraction for this page&quot;&gt;
              ⚡ Rerun &lt;span style=&quot;font-size:9.5px; opacity:0.75; font-weight:600; background:var(--border); padding:1px 4px; border-radius:3px;&quot;&gt;Admin&lt;/span&gt;
            &lt;/button&gt;
            &lt;button style=&quot;background:var(--success); color:#fff; border:none; padding:3px 9px; border-radius:5px; font-size:11px; font-weight:700; cursor:pointer;&quot; title=&quot;General users proofread &amp;amp; edit text, then mark verified&quot;&gt;
              ✓ Mark Verified &amp;amp; Next
            &lt;/button&gt;
          &lt;/div&gt;
        &lt;/div&gt;

        &lt;!-- TipTap WYSIWYG Editor Ribbon --&gt;
        &lt;div id=&quot;editorRibbon&quot; class=&quot;editor-ribbon&quot;&gt;
          &lt;div class=&quot;ribbon-group&quot;&gt;
            &lt;button class=&quot;ribbon-btn&quot; title=&quot;Undo (Ctrl+Z)&quot;&gt;↶&lt;/button&gt;
            &lt;button class=&quot;ribbon-btn&quot; title=&quot;Redo (Ctrl+Y)&quot;&gt;↷&lt;/button&gt;
          &lt;/div&gt;
          &lt;div class=&quot;ribbon-divider&quot;&gt;&lt;/div&gt;
          &lt;select class=&quot;ribbon-select&quot; title=&quot;Block Style&quot;&gt;
            &lt;option&gt;Heading 2&lt;/option&gt;
            &lt;option&gt;Paragraph&lt;/option&gt;
            &lt;option&gt;Heading 1&lt;/option&gt;
          &lt;/select&gt;
          &lt;div class=&quot;ribbon-divider&quot;&gt;&lt;/div&gt;
          &lt;div class=&quot;ribbon-group&quot;&gt;
            &lt;button class=&quot;ribbon-btn active&quot; title=&quot;Bold (Ctrl+B)&quot;&gt;B&lt;/button&gt;
            &lt;button class=&quot;ribbon-btn&quot; title=&quot;Italic (Ctrl+I)&quot;&gt;I&lt;/button&gt;
            &lt;button class=&quot;ribbon-btn&quot; title=&quot;Underline (Ctrl+U)&quot;&gt;U&lt;/button&gt;
          &lt;/div&gt;
          &lt;div class=&quot;ribbon-divider&quot;&gt;&lt;/div&gt;
          &lt;div class=&quot;ribbon-group&quot;&gt;
            &lt;button class=&quot;ribbon-btn&quot; title=&quot;Bullet List&quot;&gt;•&lt;/button&gt;
            &lt;button class=&quot;ribbon-btn&quot; title=&quot;Numbered List&quot;&gt;1.&lt;/button&gt;
            &lt;button class=&quot;ribbon-btn&quot; title=&quot;Table formatting&quot;&gt;▦&lt;/button&gt;
          &lt;/div&gt;
          &lt;div class=&quot;ribbon-divider&quot;&gt;&lt;/div&gt;
          &lt;button class=&quot;ribbon-btn-suspect&quot; title=&quot;2 suspect low-confidence tokens flagged on this page&quot;&gt;
            ⚠️ 2 Suspect Values Flagged
          &lt;/button&gt;
        &lt;/div&gt;

        &lt;div class=&quot;editor-viewport&quot;&gt;
          &lt;div id=&quot;editorDoc&quot; class=&quot;editor-doc&quot; contenteditable=&quot;true&quot;&gt;
            &lt;div style=&quot;margin-bottom:14px; padding:7px 11px; background:var(--primary-subtle); border:1px solid var(--primary-border); border-radius:6px; font-size:11.5px; color:var(--primary); display:flex; align-items:center; justify-content:space-between;&quot;&gt;
              &lt;div style=&quot;display:flex; align-items:center; gap:6px;&quot;&gt;
                &lt;span&gt;✏️&lt;/span&gt;
                &lt;span&gt;&lt;strong&gt;Proofreading &amp;amp; Editing Active:&lt;/strong&gt; Click anywhere to edit text or fix flagged numbers.&lt;/span&gt;
              &lt;/div&gt;
              &lt;span style=&quot;font-size:10px; font-weight:700; background:rgba(79,70,229,0.12); padding:2px 6px; border-radius:4px;&quot;&gt;TipTap WYSIWYG&lt;/span&gt;
            &lt;/div&gt;

            &lt;h2&gt;Payment Milestone Schedule&lt;/h2&gt;
            &lt;p&gt;Milestone 1: &lt;mark style=&quot;background:var(--warning-bg); color:var(--text-main); border-bottom:2px solid var(--warning); padding:1px 4px; border-radius:3px; font-weight:700; cursor:text;&quot; title=&quot;Suspect OCR value (71% confidence) · Click to edit directly&quot;&gt;$1,250,000.00&lt;/mark&gt; upon approval of DPR.&lt;/p&gt;
            &lt;p&gt;Milestone 2: &lt;mark style=&quot;background:var(--warning-bg); color:var(--text-main); border-bottom:2px solid var(--warning); padding:1px 4px; border-radius:3px; font-weight:700; cursor:text;&quot; title=&quot;Suspect OCR value (73% confidence) · Click to edit directly&quot;&gt;$3,500,000.00&lt;/mark&gt; upon delivery of machinery.&lt;/p&gt;
          &lt;/div&gt;
        &lt;/div&gt;
      &lt;/section&gt;`;

// Normalize linebreaks for matching
const normContent = content.replace(/\r\n/g, '\n');
const normOld = oldEditorBlock.replace(/\r\n/g, '\n');

if (normContent.includes(normOld)) {
  console.log('Found exact match for editor block!');
  content = normContent.replace(normOld, newEditorBlock.replace(/\r\n/g, '\n'));
  
  // Also add toggleEditMode script inside Slide 3's script block
  const oldScriptHook = `window.toggleHudMinimize = function(btn) {`;
  const newScriptHook = `window.toggleEditMode = function() {
    const doc = document.getElementById('editorDoc');
    const ribbon = document.getElementById('editorRibbon');
    const editBtn = document.getElementById('toggleEditBtn');
    const editLabel = document.getElementById('editLabel');
    const editDot = document.getElementById('editDot');
    const saveStatus = document.getElementById('saveStatus');
    if (!doc || !ribbon || !editBtn) return;
    const isEditing = doc.getAttribute('contenteditable') === 'true';
    if (isEditing) {
      doc.setAttribute('contenteditable', 'false');
      ribbon.style.display = 'none';
      editBtn.style.background = 'var(--bg-subtle)';
      editBtn.style.borderColor = 'var(--border)';
      editBtn.style.color = 'var(--text-muted)';
      if (editLabel) editLabel.textContent = '👁 View Only';
      if (editDot) editDot.style.background = 'var(--text-muted)';
      if (saveStatus) saveStatus.textContent = '● Read Only';
    } else {
      doc.setAttribute('contenteditable', 'true');
      ribbon.style.display = 'flex';
      editBtn.style.background = 'var(--primary-subtle)';
      editBtn.style.borderColor = 'var(--primary-border)';
      editBtn.style.color = 'var(--primary)';
      if (editLabel) editLabel.textContent = '✎ Edit Mode: ON';
      if (editDot) editDot.style.background = 'var(--primary)';
      if (saveStatus) saveStatus.textContent = '● Autosaved';
      doc.focus();
    }
  };

  window.toggleHudMinimize = function(btn) {`;

  if (content.includes(oldScriptHook)) {
    console.log('Found script hook, adding toggleEditMode!');
    content = content.replace(oldScriptHook, newScriptHook);
  } else {
    console.log('Script hook not found!');
  }

  // Also update Fix Rationale box in Slide 3
  const oldRationale = `<span class="fix-badge">Fix: Real Page-Level Metrics</span>
          <span class="fix-text">Displays actual backend page confidence (e.g. 71% · Needs Review); zero fictional descriptions.</span>`;
  const newRationale = `<span class="fix-badge">Fix: Real Page-Level Metrics</span>
          <span class="fix-text">Displays actual backend page confidence (e.g. 71% · Needs Review); zero fictional descriptions.</span>
          <span class="fix-badge enhanced">✨ New: Enabled WYSIWYG Editor</span>
          <span class="fix-text">Explicit [✎ Edit Mode: ON] toggle with rich formatting ribbon and highlighted suspect tokens allows immediate in-place correction of low-confidence OCR text.</span>`;
  if (content.includes(oldRationale)) {
    console.log('Found rationale hook, updating fix points!');
    content = content.replace(oldRationale, newRationale);
  }

  fs.writeFileSync(targetPath, content, 'utf8');
  console.log('Successfully updated mockup file!');
} else {
  console.log('Could not find match for oldEditorBlock!');
}
