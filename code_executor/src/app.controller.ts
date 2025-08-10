import {
  Controller,
  Post,
  Get,
  Param,
  Body,
  UploadedFile,
  UseInterceptors,
  Query,
  Res,
  StreamableFile,
  NotFoundException,
  HttpCode,
  HttpStatus,
  ParseFilePipe,
  MaxFileSizeValidator,
  RequestTimeoutException,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { PyodideSessionService } from './pyodide-session.service';
import type { Response } from 'express';
import {
  ApiTags,
  ApiOperation,
  ApiResponse,
  ApiConsumes,
  ApiBody,
  ApiParam,
} from '@nestjs/swagger';
import {
  CreateSessionResponseDto,
  DownloadFileQueryDto,
  ExecuteCodeDto,
  ExecuteCodeResponseDto,
  UploadFileResponseDto,
} from './dtos';

const MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024; // 100 MB
const EXECUTION_TIMEOUT_MS = 6000; // 6 seconds

@ApiTags('Pyodide Sessions')
@Controller('sessions')
export class PyodideSessionController {
  constructor(private readonly sessionService: PyodideSessionService) { }

  @Post()
  @ApiOperation({ summary: 'Create a new Pyodide session' })
  @ApiResponse({ status: 201, type: CreateSessionResponseDto })
  async createSession(): Promise<CreateSessionResponseDto> {
    const sessionId = await this.sessionService.createSession();
    return {
      message: 'Session created successfully.',
      sessionId,
    };
  }

  @Post(':sessionId/files')
  @UseInterceptors(FileInterceptor('file')) // The key 'file' is important
  @ApiConsumes('multipart/form-data')
  @ApiOperation({ summary: 'Upload a file to a session (max 100MB)' })
  @ApiResponse({ status: 201, type: UploadFileResponseDto })
  @ApiBody({
    description: 'File to upload',
    schema: {
      type: 'object',
      properties: {
        file: { 
          type: 'string',
          format: 'binary',
        },
      },
    },
  })
  // -----------------------------
  async uploadFile(
    @Param('sessionId') sessionId: string,
    @UploadedFile(
      new ParseFilePipe({
        validators: [
          new MaxFileSizeValidator({ maxSize: MAX_FILE_SIZE_BYTES }),
        ],
      }),
    )
    file: Express.Multer.File,
  ): Promise<UploadFileResponseDto> {
    const filename = await this.sessionService.uploadFile(sessionId, file);
    return {
      message: `File "${filename}" uploaded to session ${sessionId} successfully.`,
      filename,
    };
  }

  @Post(':sessionId/execute')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: 'Execute Python code in a session (max 15 seconds)' })
  @ApiResponse({ status: 200, type: ExecuteCodeResponseDto })
  async executeCode(
    @Param('sessionId') sessionId: string,
    @Body() executeCodeDto: ExecuteCodeDto,
  ): Promise<ExecuteCodeResponseDto> {
    try {
      // The service now handles the timeout internally. We just pass the value.
      const result = await this.sessionService.executeCode(
        sessionId,
        executeCodeDto.code,
        EXECUTION_TIMEOUT_MS,
      );
      return { result };
    } catch (error) {
      // If the service throws a timeout exception, re-throw it as the correct HTTP error.
      if (error instanceof RequestTimeoutException) {
        throw error;
      }
      // Forward other errors from the worker (e.g., Python exceptions).
      return { error: error.message };
    }
  }

  @Get(':sessionId/files')
  @ApiOperation({ summary: 'Download a file from a session' })
  @ApiResponse({ status: 200, description: 'The file is returned as a binary stream.' })
  async downloadFile(
    @Param('sessionId') sessionId: string,
    @Query() query: DownloadFileQueryDto,
    @Res({ passthrough: true }) res: Response,
  ): Promise<StreamableFile> {
    try {
      const data = await this.sessionService.downloadFile(sessionId, query.path);
      res.set({
        'Content-Type': 'application/octet-stream',
        'Content-Disposition': `attachment; filename="${query.path}"`,
      });
      // Convert the Uint8Array from the worker back to a Buffer for StreamableFile
      return new StreamableFile(Buffer.from(data));
    } catch (error) {
      throw new NotFoundException(error.message);
    }
  }
}