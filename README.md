# Django Seat Reservations

API pequeña para reservar un cupo en un taller. Demuestra transacciones, bloqueo de filas, idempotencia y pruebas concurrentes con PostgreSQL real.

Dos modelos, tres endpoints y funciones de negocio con el ORM de Django. Sin autenticación, pagos, colas ni interfaz gráfica.

## Ejecutar

Requiere Docker con Compose. Python 3.13 y PostgreSQL 16 se ejecutan en contenedores; no necesitas instalarlos en el equipo.

Desde esta carpeta:

```sh
docker compose build app
docker compose run --rm app python manage.py migrate
docker compose run --rm app python manage.py seed_demo
docker compose up -d app
```

El comando de ejemplo imprime el identificador de la actividad y crea dos cupos. Repetirlo conserva la actividad y sus reservas. En una base nueva el identificador es `1`; ajusta los ejemplos si el comando imprime otro valor.

La API escucha en `http://127.0.0.1:8000`. PostgreSQL solo está disponible dentro de la red del proyecto, con volumen persistente y credenciales locales de demostración. No comparte datos con otros proyectos.

Para detener los procesos conservando los datos:

```sh
docker compose stop
```

Para retomarlos, ejecuta `docker compose up -d app`. El servidor de Django y la configuración suministrada son para desarrollo local.

## API

| Método | Ruta | Resultado |
| --- | --- | --- |
| GET | `/api/activities/{id}/` | Capacidad, reservas activas y cupos disponibles |
| POST | `/api/activities/{id}/reservations/` | Crear una reserva o recuperar una existente por clave |
| POST | `/api/activities/{id}/reservations/{reservation_id}/cancel/` | Cancelar sin borrar; repetir devuelve el estado actual |

### Consultar disponibilidad

```sh
curl -i http://127.0.0.1:8000/api/activities/1/
```

En una actividad nueva:

```json
{
  "id": 1,
  "name": "Taller de PostgreSQL",
  "capacity": 2,
  "active_reservations": 0,
  "available_seats": 2
}
```

### Reservar y repetir la solicitud

```sh
curl -i -X POST http://127.0.0.1:8000/api/activities/1/reservations/ \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: ana-request-1' \
  -d '{"participant_name":"Ana"}'
```

Devuelve `201` al crear:

```json
{"id": 1, "activity_id": 1, "participant_name": "Ana", "status": "active"}
```

Repetir el mismo comando devuelve `200` y el mismo identificador, incluso si la actividad se llenó después. Cambiar el nombre conservando la clave devuelve `409`.

### Cancelar

Usa el identificador de reserva que devolvió la creación:

```sh
curl -i -X POST http://127.0.0.1:8000/api/activities/1/reservations/1/cancel/
```

Devuelve `200` con estado `cancelled`. Repetirlo conserva el estado y no libera otro cupo. Repetir la creación con la clave original devuelve esa misma reserva cancelada. Para reservar de nuevo se necesita otra clave.

### Validación y errores

- `participant_name`: obligatorio, hasta 120 caracteres; se recortan espacios iniciales y finales. El nombre validado se compara exactamente al repetir la clave; distingue mayúsculas.
- `Idempotency-Key`: obligatoria, entre 1 y 128 caracteres ASCII alfanuméricos, puntos, guiones, guiones bajos o dos puntos. Los errores de esta cabecera aparecen bajo el campo `key`.
- `400`: entrada inválida o JSON mal formado.
- `404`: actividad o reserva inexistente, incluida una reserva de otra actividad.
- `409`: actividad llena o clave reutilizada con un nombre diferente.

La clave es única por actividad y se conserva mientras exista la reserva. Solo las creaciones exitosas guardan claves; una solicitud rechazada por falta de cupos puede volver a intentarse después. La idempotencia compara los datos aceptados por la API tras validación, no el JSON textual. No verifica identidad ni impide que una persona use varias claves.

## Qué demuestra el diseño

[services.py](reservations/services.py) contiene las dos operaciones de negocio. Cada una abre `transaction.atomic()` y adquiere `select_for_update()` sobre la fila de la actividad antes de consultar o modificar reservas.

La creación comprueba primero si existe la clave, después cuenta las reservas activas y finalmente inserta. Una segunda transacción para esa actividad espera el bloqueo y ve el resultado confirmado de la primera. La cancelación toma el mismo bloqueo en el mismo orden. Si una operación falla, se revierte su transacción.

La lectura de disponibilidad usa una sola consulta agregada: capacidad y conteo pertenecen al mismo snapshot. Es información del momento de la consulta; no promete que un cupo siga disponible hasta una petición posterior.

[models.py](reservations/models.py) declara restricciones de base de datos para capacidad positiva, clave única por actividad y estados válidos. La regla de capacidad entre tablas depende del servicio transaccional: escrituras directas con SQL u ORM que lo omitan podrían excederla. No se introduce un trigger para este alcance.

El bloqueo serializa operaciones de una misma actividad y limita su rendimiento bajo mucha contención. Actividades distintas tienen filas de bloqueo distintas. Usamos el aislamiento predeterminado de Django con PostgreSQL (`READ COMMITTED`); no necesitamos un contador duplicado de disponibilidad, una cola ni reintentos automáticos.

Las vistas manejan HTTP, los serializers validan entradas y las funciones de negocio utilizan el ORM directamente. No hay repositorios, interfaces ni un framework de arquitectura adicional.

## Pruebas reproducibles

```sh
docker compose run --rm app python manage.py test --verbosity 2 --noinput
```

Solo concurrencia:

```sh
docker compose run --rm app python manage.py test reservations.tests.test_concurrency --verbosity 2 --noinput
```

El runner crea `test_reservations`, aplica las migraciones y elimina esa base al terminar. La base local `reservations` y los datos de ejemplo se conservan. El usuario de PostgreSQL del entorno local puede crear la base de pruebas.

La suite contiene 27 pruebas:

- **19 de API:** disponibilidad, límites, idempotencia, cancelación, nombres, cabeceras, JSON y recursos inexistentes.
- **3 de restricciones:** inserciones reales que PostgreSQL rechaza, sin pasar por la API.
- **5 de concurrencia:** `TransactionTestCase`, hilos con conexiones independientes, `pg_backend_pid()` para verificar sesiones distintas y una barrera de salida. Se usan llamadas HTTP en proceso con `APIClient`; el tráfico no sale por un socket, pero las transacciones y bloqueos son reales en PostgreSQL. No hay SQLite ni mocks de persistencia.

| Escenario concurrente | Aserción |
| --- | --- |
| 20 solicitudes distintas, 2 cupos | Exactamente 2 respuestas `201`, 18 respuestas `409` y 2 reservas |
| 20 solicitudes con la misma clave y nombre | Una respuesta `201`, 19 respuestas `200` y el mismo ID |
| Misma clave con 2 nombres diferentes | Una creación y un conflicto |
| 20 cancelaciones de la misma reserva | Todas devuelven `200`; queda una reserva cancelada |
| Cancelación y 19 creaciones, capacidad 1 | Nunca queda más de una reserva activa; el cupo liberado puede ocuparse |

La carrera cancelación/creación no impone un ganador: una creación anterior a la cancelación puede recibir `409`. Se verifican los resultados válidos y el estado persistido. Las excepciones de los hilos se propagan; no se convierten en conflictos esperados. La barrera y los tiempos límite evitan usar pausas arbitrarias para coordinar las operaciones.

Comprobaciones adicionales:

```sh
docker compose run --rm app python manage.py check
docker compose run --rm app python manage.py makemigrations --check --dry-run
```

GitHub Actions está configurado con Python 3.13, PostgreSQL 16 y la misma suite. Las versiones de las dependencias están fijadas en `requirements.txt`.

## Límites

La capacidad es fija y cada reserva ocupa un cupo. No se implementan administración de actividades, cambio de capacidad, vencimiento, lista de espera ni borrado de reservas. La consulta puede terminar saturada si hay demasiadas solicitudes; el entorno configura límites de espera de conexión, bloqueo y consulta, pero no ofrece un protocolo de recuperación de fallos de infraestructura.

Referencias: [bloqueos con `select_for_update`](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update) y [pruebas transaccionales de Django](https://docs.djangoproject.com/en/5.2/topics/testing/tools/#transactiontestcase).
