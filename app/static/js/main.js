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

    formResult.textContent = "Se agendó la cita.";
    formResult.classList.add("is-visible");
  });
}
