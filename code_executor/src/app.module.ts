import { Module } from '@nestjs/common';
import { PyodideSessionController } from './app.controller';
import { PyodideSessionService } from './pyodide-session.service';

@Module({
  imports: [],
  controllers: [PyodideSessionController],
  providers: [PyodideSessionService],
})
export class AppModule {}
