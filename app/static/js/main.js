const menuButton = document.querySelector(".menu-toggle");
const navigation = document.querySelector("#site-nav");

if (menuButton && navigation) {
  menuButton.addEventListener("click", () => {
    const isOpen = menuButton.getAttribute("aria-expanded") === "true";
    menuButton.setAttribute("aria-expanded", String(!isOpen));
    navigation.classList.toggle("is-open", !isOpen);
  });

  navigation.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => {
      menuButton.setAttribute("aria-expanded", "false");
      navigation.classList.remove("is-open");
    });
  });
}

const bookingForm = document.querySelector("#booking-form");

if (bookingForm) {
  bookingForm.addEventListener("submit", () => {
    const submitButton = bookingForm.querySelector("#booking-submit");
    if (!submitButton) {
      return;
    }

    submitButton.disabled = true;
    submitButton.textContent = "Enviando solicitud…";
    bookingForm.setAttribute("aria-busy", "true");
  });
}

const adminPassword = document.querySelector("#admin-password");
const passwordToggle = document.querySelector("#password-toggle");

if (adminPassword && passwordToggle) {
  passwordToggle.addEventListener("click", () => {
    const showPassword = adminPassword.type === "password";
    adminPassword.type = showPassword ? "text" : "password";
    passwordToggle.setAttribute("aria-pressed", String(showPassword));
    passwordToggle.textContent = showPassword
      ? "Ocultar contraseña"
      : "Mostrar contraseña";
  });
}
