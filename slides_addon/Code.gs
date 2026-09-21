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
    presentationName: presentation.getName(),
    savedToken: getToken()
  };
}

// The sidebar iframe is cross-origin from googleusercontent.com, so it can't
// rely on browser storage surviving reloads reliably. PropertiesService
// persists the token server-side, scoped to this user + this script.
function saveToken(token) {
  PropertiesService.getUserProperties().setProperty('slidemakr_token', token);
}

function getToken() {
  return PropertiesService.getUserProperties().getProperty('slidemakr_token') || '';
}

function clearToken() {
  PropertiesService.getUserProperties().deleteProperty('slidemakr_token');
}
