// src/pyodide-session/pyodide-session.dto.ts
import { ApiProperty } from '@nestjs/swagger';

// --- Request DTOs ---

export class ExecuteCodeDto {
  @ApiProperty({
    example: 'import numpy as np\na = np.array([1, 2, 3])\na * 2',
    description: 'The Python code snippet to execute within the session.',
  })
  code: string;
}

export class DownloadFileQueryDto {
  @ApiProperty({
    example: 'results.csv',
    description: 'The full path to the file within the Pyodide virtual filesystem.',
  })
  path: string;
}

// --- Response DTOs ---

export class CreateSessionResponseDto {
  @ApiProperty({ example: 'Session created successfully.' })
  message: string;

  @ApiProperty({
    example: 'a1b2c3d4-e5f6-g7h8-i9j0-k1l2m3n4o5p6',
    description: 'The unique ID for the newly created session.',
  })
  sessionId: string;
}

export class UploadFileResponseDto {
  @ApiProperty({ example: 'File "data.csv" uploaded successfully.' })
  message: string;

  @ApiProperty({
    example: 'data.csv',
    description: 'The name of the uploaded file.',
  })
  filename: string;
}

export class ExecuteCodeResponseDto {
  @ApiProperty({
    required: false,
    description: 'The result of the Python code execution. Can be any JSON-serializable type.',
    example: { data: [1, 2, 3] },
  })
  result?: any;

  @ApiProperty({
    required: false,
    description: 'An error message if the Python code failed to execute.',
    example: "NameError: name 'np' is not defined",
  })
  error?: string;
}
