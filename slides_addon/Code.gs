// One place to change the backend host. See docs/slides-addon.md for how to
// swap this when a custom domain (e.g. slidemakr.com) is ready to cut over.
const SLIDEMAKR_BASE_URL = 'https://slidemakr-72711045873.us-central1.run.app';

function onOpen(e) {
  SlidesApp.getUi()
    .createAddonMenu()
    .addItem('Open SlideMakr', 'showSidebar')
    .addToUi();
}

function onInstall(e) {
  onOpen(e);
}

function showSidebar() {
  const html = HtmlService.createHtmlOutputFromFile('Sidebar')
    .setTitle('SlideMakr');
  SlidesApp.getUi().showSidebar(html);
}

function getAddonConfig() {
  const presentation = SlidesApp.getActivePresentation();
  return {
    backendUrl: SLIDEMAKR_BASE_URL,
    activePresentationId: presentation.getId(),
    presentationName: presentation.getName()
  };
}
