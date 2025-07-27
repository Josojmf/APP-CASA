const socket = io();

socket.on('new_data', function(data) {
    console.log("Nuevo dato recibido en tiempo real:", data);
    const container = document.getElementById("dynamicContent");
    const el = document.createElement("div");
    el.textContent = "Nuevo dato: " + JSON.stringify(data);
    container.appendChild(el);
});

document.getElementById("actionBtn").addEventListener("click", () => {
    alert("¡Hola desde el botón! 🎉");
});
