
        $(window).on("load", function () {
            var soportaCanvas = Ciel.MPC.WebPlayer.Navegador.SoportaCanvas();
            var soportaVideo = Ciel.MPC.WebPlayer.Navegador.SoportaVideo();
            if ((soportaCanvas == false) || (soportaVideo == false)) {
                document.getElementById('lblError').innerHTML = 'Se está intentando usar un navegador que no es compatible con html5, el contenido no se mostrará correctamente.';
            }
        });
        window.addEventListener("beforeunload", function (e) {
            if (Ciel.MPC.WebPlayer.FuenteDatos && Ciel.MPC.WebPlayer.FuenteDatos.FuenteDatosIniciadas && Ciel.MPC.WebPlayer.FuenteDatos.FuenteDatosIniciadas.length > 0) {
                var fuenteDatos = Ciel.MPC.WebPlayer.FuenteDatos.FuenteDatosIniciadas[0];
                if (fuenteDatos.IdTablero !== undefined && fuenteDatos.Configuracion.TipoActualizacion !== undefined && fuenteDatos.Configuracion.TipoActualizacion == 1) {
                    Ciel.MPC.WebPlayer.UI.Servicio.PublicadorD5.DesvincularTableroCliente(fuenteDatos.IdTablero, function () {
                    },
                        function () { });
                }
            }
        });

        $('#form1').submit(function () {
            return false;
        });


    