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

const specialtySelect = document.querySelector("#specialty");
const professionalSelect = document.querySelector("#professional");

if (specialtySelect && professionalSelect) {
  specialtySelect.addEventListener("change", () => {
    const specialty = specialtySelect.value;
    const professionalOptions = professionalSelect.querySelectorAll("option[data-specialty]");

    professionalSelect.value = "";
    professionalSelect.disabled = !specialty;

    professionalOptions.forEach((option) => {
      option.hidden = option.dataset.specialty !== specialty;
    });

    professionalSelect.options[0].textContent = specialty
      ? "Selecciona un perfil demostrativo"
      : "Primero selecciona una especialidad";
  });
}

const dateInput = document.querySelector("#date");

if (dateInput) {
  const now = new Date();
  const localToday = new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 10);
  dateInput.min = localToday;
}

const bookingForm = document.querySelector("#booking-form");
const demoSubmit = document.querySelector("#demo-submit");
const formResult = document.querySelector("#form-result");

if (bookingForm && demoSubmit && formResult) {
  demoSubmit.addEventListener("click", () => {
    if (!bookingForm.reportValidity()) {
      return;
    }

    formResult.textContent = "Demostración completada. No se enviaron ni guardaron datos y no se reservó una cita.";
    formResult.classList.add("is-visible");
  });
}
