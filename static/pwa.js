let deferredInstallPrompt = null;

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;

  const button = document.getElementById("pwa-install-button");
  if (button) {
    button.style.display = "inline-block";
  }
});

async function installPwa() {
  if (!deferredInstallPrompt) {
    alert("Instalace není v tomto prohlížeči aktuálně dostupná. Na mobilu otevři menu prohlížeče a zvol „Přidat na plochu“.");
    return;
  }

  deferredInstallPrompt.prompt();
  await deferredInstallPrompt.userChoice;
  deferredInstallPrompt = null;

  const button = document.getElementById("pwa-install-button");
  if (button) {
    button.style.display = "none";
  }
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/static/service-worker.js")
      .catch((error) => console.log("Service worker registration failed:", error));
  });
}
