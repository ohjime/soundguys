// Mounts EasyMDE on admin textareas rendered by explore.widgets.EasyMDEWidget.
// toolbar/autoDownloadFontAwesome are off so the admin makes zero external
// requests; side-by-side preview is still available via Cmd/Ctrl-P.
document.addEventListener("DOMContentLoaded", () => {
  document
    .querySelectorAll("textarea[data-easymde]:not([data-easymde-mounted])")
    .forEach((el) => {
      el.setAttribute("data-easymde-mounted", "true");
      new EasyMDE({
        element: el,
        spellChecker: false,
        status: false,
        minHeight: "320px",
        toolbar: false,
        autoDownloadFontAwesome: false,
      });
    });
});
