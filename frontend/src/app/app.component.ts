import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

/**
 * Componente raiz. Solo monta el `router-outlet`; la navegacion entre
 * `login`, `2fa` y `dashboard` la gobierna el enrutador.
 */
@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
export class AppComponent {}
