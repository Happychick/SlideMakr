const SLIDEMAKR_BACKEND_URL = 'https://slidemakr.com';

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
    backendUrl: SLIDEMAKR_BACKEND_URL,
    activePresentationId: presentation.getId(),
    presentationName: presentation.getName()
  };
}
