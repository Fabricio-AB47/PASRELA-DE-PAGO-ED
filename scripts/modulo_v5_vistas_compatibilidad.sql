USE [INTECEDUCONTINUA];
GO

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

IF OBJECT_ID(N'[edu].[EstudiantePrincipalSnapshot]', N'U') IS NULL
BEGIN
    CREATE TABLE [edu].[EstudiantePrincipalSnapshot] (
        [CodigoEstud] decimal(18,0) NOT NULL PRIMARY KEY,
        [CedulaEst] varchar(50) NULL,
        [ApellidosNombre] nvarchar(300) NULL,
        [CorreoPersonal] varchar(150) NULL,
        [CorreoIntec] varchar(150) NULL,
        [UsuarioLogin] varchar(100) NULL,
        [FechaSincronizacion] datetime2(0) NOT NULL
            CONSTRAINT [DF_edu_EstudianteSnapshot_Fecha] DEFAULT sysdatetime()
    );
END;

IF OBJECT_ID(N'[edu].[DocentePrincipalSnapshot]', N'U') IS NULL
BEGIN
    CREATE TABLE [edu].[DocentePrincipalSnapshot] (
        [CodigoDocente] decimal(18,0) NOT NULL PRIMARY KEY,
        [CedulaDoc] varchar(50) NULL,
        [ApellidosNombre] nvarchar(300) NULL,
        [CorreoPersonal] varchar(150) NULL,
        [CorreoIntec] varchar(150) NULL,
        [UsuarioLogin] varchar(100) NULL,
        [FechaSincronizacion] datetime2(0) NOT NULL
            CONSTRAINT [DF_edu_DocenteSnapshot_Fecha] DEFAULT sysdatetime()
    );
END;
GO

CREATE OR ALTER VIEW [edu].[VW_CorteCursoDetalle]
AS
SELECT
    CC.[CorteCursoId],
    CC.[CorteId],
    CAST('EDUCACION_CONTINUA' AS varchar(20)) AS [TipoOferta],
    CC.[Cod_AnioBasica],
    CAST(N'Educación continua' AS nvarchar(240)) AS [NombreCarrera],
    CC.[CodigoPeriodo],
    LTRIM(RTRIM(CONVERT(varchar(100), PE.[Detalle_Periodo]))) COLLATE DATABASE_DEFAULT AS [PeriodoDetalle],
    CC.[CodigoMateria],
    LTRIM(RTRIM(CONVERT(varchar(50), P.[cod_materia]))) COLLATE DATABASE_DEFAULT AS [CodigoMateriaTexto],
    CC.[CodCurso],
    COALESCE(
        NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(400), P.[Nomb_Materia]))), N''),
        CONCAT(N'Curso ', CONVERT(nvarchar(30), CC.[CodigoMateria]))
    ) COLLATE DATABASE_DEFAULT AS [NombreCursoMateria],
    COALESCE(
        NULLIF(LTRIM(RTRIM(CC.[Observacion])), N''),
        CONCAT(N'Corte ', CONVERT(nvarchar(30), CC.[CorteId]))
    ) COLLATE DATABASE_DEFAULT AS [NombreCorte],
    CC.[CupoMaximo],
    CC.[PermiteSobrecupo],
    CC.[ValorCurso],
    CC.[NotaMinima],
    CC.[NotaMaxima],
    CC.[PorcentajeMinAsistencia],
    CC.[RequierePagoCompleto],
    CC.[RequierePaseNotas],
    CC.[GeneraCertificado],
    CC.[UsaTeams],
    CC.[EstadoCorteEdu],
    COALESCE(CC.[FechaInicioOverride], PE.[fechain]) AS [FechaInicio],
    COALESCE(CC.[FechaFinOverride], PE.[fechafin]) AS [FechaFin],
    CC.[UsuarioRegistro],
    CC.[FechaRegistro],
    CC.[UsuarioModifica],
    CC.[FechaModifica]
FROM [edu].[CorteCurso] CC
LEFT JOIN [ref].[PENSUM] P
  ON P.[Cod_AnioBasica] = CC.[Cod_AnioBasica]
 AND P.[codigo_materia] = CC.[CodigoMateria]
LEFT JOIN [ref].[PERIODO] PE
  ON PE.[cod_periodo] = CC.[CodigoPeriodo];
GO

CREATE OR ALTER VIEW [edu].[VW_MatriculaEstudianteCompleta]
AS
SELECT
    CE.[EstudianteCorteId],
    CE.[CorteId],
    CE.[CodigoEstud],
    CE.[CorteEstudianteIdPrincipal],
    CE.[EstadoMatricula],
    CE.[TipoIngreso],
    CE.[FechaMatricula],
    COALESCE(SP.[CedulaEst], EP.[CedulaEst]) COLLATE DATABASE_DEFAULT AS [CedulaEst],
    COALESCE(SP.[ApellidosNombre], EP.[ApellidosNombre]) COLLATE DATABASE_DEFAULT AS [ApellidosNombre],
    COALESCE(SP.[CorreoPersonal], EP.[CorreoPersonal]) COLLATE DATABASE_DEFAULT AS [CorreoPersonal],
    COALESCE(SP.[CorreoIntec], EP.[CorreoIntec]) COLLATE DATABASE_DEFAULT AS [CorreoIntec],
    COALESCE(SP.[UsuarioLogin], EP.[UsuarioLogin]) COLLATE DATABASE_DEFAULT AS [UsuarioLogin],
    EP.[UsuarioSisLogin],
    CE.[UsuarioRegistro],
    CE.[FechaRegistro],
    CE.[UsuarioModifica],
    CE.[FechaModifica],
    CE.[Observacion]
FROM [edu].[CorteEstudiante] CE
LEFT JOIN [edu].[EstudiantePrincipalSnapshot] SP
  ON SP.[CodigoEstud] = CE.[CodigoEstud]
LEFT JOIN [edu].[VW_EstudiantePrincipal] EP
  ON EP.[CodigoEstud] = CE.[CodigoEstud];
GO

CREATE OR ALTER VIEW [edu].[VW_MatriculaDocenteCompleta]
AS
SELECT
    CD.[DocenteCorteId],
    CD.[CorteId],
    CD.[CodigoDocente],
    CD.[RolDocente],
    CD.[EstadoDocenteCorte],
    CD.[FechaMatricula],
    COALESCE(SP.[CedulaDoc], DP.[CedulaDoc]) COLLATE DATABASE_DEFAULT AS [CedulaDoc],
    COALESCE(SP.[ApellidosNombre], DP.[ApellidosNombre]) COLLATE DATABASE_DEFAULT AS [ApellidosNombre],
    COALESCE(SP.[CorreoPersonal], DP.[CorreoPersonal]) COLLATE DATABASE_DEFAULT AS [CorreoPersonal],
    COALESCE(SP.[CorreoIntec], DP.[CorreoIntec]) COLLATE DATABASE_DEFAULT AS [CorreoIntec],
    COALESCE(SP.[UsuarioLogin], DP.[UsuarioLogin]) COLLATE DATABASE_DEFAULT AS [UsuarioLogin],
    DP.[UsuarioSisLogin],
    CD.[UsuarioRegistro],
    CD.[FechaRegistro],
    CD.[UsuarioModifica],
    CD.[FechaModifica],
    CD.[Observacion]
FROM [edu].[CorteDocente] CD
LEFT JOIN [edu].[DocentePrincipalSnapshot] SP
  ON SP.[CodigoDocente] = CD.[CodigoDocente]
LEFT JOIN [edu].[VW_DocentePrincipal] DP
  ON DP.[CodigoDocente] = CD.[CodigoDocente];
GO

CREATE OR ALTER VIEW [edu].[VW_CupoCorte]
AS
SELECT
    CC.[CorteId],
    CC.[CupoMaximo],
    CC.[PermiteSobrecupo],
    COUNT(CASE
        WHEN CE.[EstadoMatricula] IN (
            'PREINSCRITO','INSCRITO','CURSANDO','BLOQUEADO_PAGO','APROBADO','FINALIZADO'
        ) THEN 1
    END) AS [TotalMatriculadosActivos],
    CASE
        WHEN CC.[PermiteSobrecupo] = 1 THEN NULL
        WHEN CC.[CupoMaximo] <= COUNT(CASE
            WHEN CE.[EstadoMatricula] IN (
                'PREINSCRITO','INSCRITO','CURSANDO','BLOQUEADO_PAGO','APROBADO','FINALIZADO'
            ) THEN 1
        END) THEN 0
        ELSE CC.[CupoMaximo] - COUNT(CASE
            WHEN CE.[EstadoMatricula] IN (
                'PREINSCRITO','INSCRITO','CURSANDO','BLOQUEADO_PAGO','APROBADO','FINALIZADO'
            ) THEN 1
        END)
    END AS [CuposDisponibles],
    CASE
        WHEN CC.[PermiteSobrecupo] = 1 THEN 'DISPONIBLE'
        WHEN CC.[CupoMaximo] <= COUNT(CASE
            WHEN CE.[EstadoMatricula] IN (
                'PREINSCRITO','INSCRITO','CURSANDO','BLOQUEADO_PAGO','APROBADO','FINALIZADO'
            ) THEN 1
        END) THEN 'LLENO'
        ELSE 'DISPONIBLE'
    END AS [EstadoCupo]
FROM [edu].[CorteCurso] CC
LEFT JOIN [edu].[CorteEstudiante] CE
  ON CE.[CorteId] = CC.[CorteId]
GROUP BY
    CC.[CorteId],
    CC.[CupoMaximo],
    CC.[PermiteSobrecupo];
GO

CREATE OR ALTER VIEW [edu].[VW_AsistenciaResumen]
AS
SELECT
    CE.[EstudianteCorteId],
    CE.[CorteId],
    CE.[CodigoEstud],
    COUNT(DISTINCT CASE
        WHEN S.[EstadoSesion] = 'REALIZADA' THEN S.[SesionId]
    END) AS [TotalSesionesRealizadas],
    COUNT(DISTINCT CASE
        WHEN S.[EstadoSesion] = 'REALIZADA'
         AND A.[CuentaParaAsistencia] = 1 THEN S.[SesionId]
    END) AS [TotalAsistencias],
    COUNT(DISTINCT CASE
        WHEN S.[EstadoSesion] = 'REALIZADA'
         AND ISNULL(A.[CuentaParaAsistencia], 0) = 0 THEN S.[SesionId]
    END) AS [TotalAusencias],
    CAST(
        CASE
            WHEN COUNT(DISTINCT CASE
                WHEN S.[EstadoSesion] = 'REALIZADA' THEN S.[SesionId]
            END) = 0 THEN 0
            ELSE 100.0 * COUNT(DISTINCT CASE
                WHEN S.[EstadoSesion] = 'REALIZADA'
                 AND A.[CuentaParaAsistencia] = 1 THEN S.[SesionId]
            END) / COUNT(DISTINCT CASE
                WHEN S.[EstadoSesion] = 'REALIZADA' THEN S.[SesionId]
            END)
        END
        AS decimal(5,2)
    ) AS [PorcentajeAsistencia]
FROM [edu].[CorteEstudiante] CE
LEFT JOIN [edu].[SesionCorte] S
  ON S.[CorteId] = CE.[CorteId]
 AND S.[EstadoSesion] <> 'CANCELADA'
LEFT JOIN [edu].[AsistenciaCorte] A
  ON A.[SesionId] = S.[SesionId]
 AND A.[EstudianteCorteId] = CE.[EstudianteCorteId]
WHERE CE.[EstadoMatricula] NOT IN ('ANULADO', 'RETIRADO')
GROUP BY
    CE.[EstudianteCorteId],
    CE.[CorteId],
    CE.[CodigoEstud];
GO

CREATE OR ALTER VIEW [fin].[VW_BalanceEstudiante]
AS
SELECT
    C.[CuentaId],
    C.[EstudianteCorteId],
    C.[CorteId],
    C.[CodigoEstud],
    C.[EstadoCuenta],
    CAST(ISNULL(SUM(CASE
        WHEN M.[EstadoMovimiento] = 'ACTIVO' AND M.[TipoMovimiento] = 'DEBE'
        THEN M.[Valor] ELSE 0
    END), 0) AS decimal(18,2)) AS [TotalDebe],
    CAST(ISNULL(SUM(CASE
        WHEN M.[EstadoMovimiento] = 'ACTIVO' AND M.[TipoMovimiento] = 'HABER'
        THEN M.[Valor] ELSE 0
    END), 0) AS decimal(18,2)) AS [TotalHaber],
    CAST(
        ISNULL(SUM(CASE
            WHEN M.[EstadoMovimiento] = 'ACTIVO' AND M.[TipoMovimiento] = 'DEBE'
            THEN M.[Valor] ELSE 0
        END), 0)
        - ISNULL(SUM(CASE
            WHEN M.[EstadoMovimiento] = 'ACTIVO' AND M.[TipoMovimiento] = 'HABER'
            THEN M.[Valor] ELSE 0
        END), 0)
        AS decimal(18,2)
    ) AS [SaldoPendiente],
    MAX(CASE
        WHEN M.[EstadoMovimiento] = 'ACTIVO' AND M.[TipoMovimiento] = 'HABER'
        THEN M.[FechaMovimiento]
    END) AS [UltimoPago]
FROM [fin].[CuentaEstudiante] C
LEFT JOIN [fin].[MovimientoCuenta] M
  ON M.[CuentaId] = C.[CuentaId]
GROUP BY
    C.[CuentaId],
    C.[EstudianteCorteId],
    C.[CorteId],
    C.[CodigoEstud],
    C.[EstadoCuenta];
GO

IF OBJECT_ID('[fin].[FacturaMovimiento]', 'U') IS NULL
BEGIN
    CREATE TABLE [fin].[FacturaMovimiento] (
        [FacturaMovimientoId] int IDENTITY(1,1) NOT NULL,
        [MovimientoId] int NOT NULL,
        [EstadoFactura] varchar(20) NOT NULL
            CONSTRAINT [DF_FIN_FACTURA_ESTADO] DEFAULT ('SUBIDA'),
        [NumeroFactura] nvarchar(100) NULL,
        [UrlDocumento] nvarchar(1000) NOT NULL,
        [NombreArchivo] nvarchar(260) NOT NULL,
        [HashDocumento] char(64) NOT NULL,
        [UsuarioRegistro] varchar(50) NOT NULL,
        [FechaRegistro] datetime2(0) NOT NULL
            CONSTRAINT [DF_FIN_FACTURA_FECHA] DEFAULT (sysdatetime()),
        [UsuarioModifica] varchar(50) NULL,
        [FechaModifica] datetime2(0) NULL,
        [Observacion] nvarchar(500) NULL,
        CONSTRAINT [PK_FIN_FACTURA_MOVIMIENTO] PRIMARY KEY ([FacturaMovimientoId]),
        CONSTRAINT [UX_FIN_FACTURA_MOVIMIENTO] UNIQUE ([MovimientoId]),
        CONSTRAINT [FK_FIN_FACTURA_MOVIMIENTO]
            FOREIGN KEY ([MovimientoId]) REFERENCES [fin].[MovimientoCuenta] ([MovimientoId]),
        CONSTRAINT [CK_FIN_FACTURA_ESTADO]
            CHECK ([EstadoFactura] IN ('SUBIDA', 'ANULADA'))
    );
    CREATE INDEX [IX_FIN_FACTURA_ESTADO_FECHA]
        ON [fin].[FacturaMovimiento] ([EstadoFactura], [FechaRegistro] DESC);
END;
GO
